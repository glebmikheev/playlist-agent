import os
import uuid

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

import gradio as gr

from playlist_agent.agent import run_agent
from playlist_agent.catalogue import Catalogue
from playlist_agent.gemini import GeminiClient, ModelError
from playlist_agent.models import PlaylistSpec
from playlist_agent.presentation import (
    describe_spec,
    export_playlist,
    profile_svg,
    table_rows,
)
from playlist_agent.settings import ROOT, Settings
from playlist_agent.storage import SessionStore, update_snapshot


def create_app(
    catalogue_path=ROOT / "data/catalogue.sqlite", session_dir=ROOT / "data/sessions"
):
    catalogue, store = Catalogue(catalogue_path), SessionStore(session_dir)

    def render(snapshot):
        attempt = snapshot.get("last_attempt", {})
        ids = snapshot.get("playlist_ids", [])
        spec_data = snapshot.get("spec")
        if not ids and attempt.get("playlist_ids"):
            ids, spec_data = attempt["playlist_ids"], attempt["spec"]
        tracks = catalogue.tracks(ids)
        spec = PlaylistSpec.model_validate(spec_data) if spec_data else None
        files = (
            export_playlist(ROOT / "data/exports", snapshot["session_id"], spec, tracks)
            if spec and tracks
            else []
        )
        status = attempt.get(
            "message", "Опиши длительность или число треков и музыкальные пожелания."
        )
        if tracks:
            status += f"\n\n**{len(tracks)} треков · {sum(track['duration_ms'] for track in tracks) / 60000:.1f} мин.**"
        if attempt.get("status") in {
            "failed",
            "error",
            "clarification",
        } and snapshot.get("playlist_ids"):
            status += "\n\nПоказан последний принятый плейлист."
        if attempt.get("changes"):
            change = attempt["changes"]
            status += f"\n\nДобавлено: {change['added']}; удалено: {change['removed']}; сохранено: {change['retained']}."
        details = {
            "status": attempt.get("status"),
            "interpreted_spec": snapshot.get("draft_spec") or spec_data,
            "validation": attempt.get("validation"),
            "metrics": attempt.get("metrics"),
        }
        return (
            snapshot.get("messages", []),
            status,
            describe_spec(
                PlaylistSpec.model_validate(snapshot.get("draft_spec") or spec_data)
            )
            if snapshot.get("draft_spec") or spec_data
            else "",
            table_rows(tracks),
            profile_svg(spec, tracks) if spec else "",
            gr.update(
                choices=[
                    (f"{i}. {track['title']} — {track['artists']}", track["track_id"])
                    for i, track in enumerate(tracks, 1)
                ],
                value=spec.locked_track_ids if spec else [],
            ),
            files,
            details,
            snapshot["session_id"],
            gr.update(
                choices=store.list_sessions(),
                value=snapshot["session_id"]
                if (store.directory / f"{snapshot['session_id']}.json").exists()
                else None,
            ),
        )

    async def submit(message, session_id, locked_ids):
        if not message.strip() or len(message) > 2000:
            raise gr.Error("Введи сообщение длиной от 1 до 2000 символов.")
        snapshot = store.load(session_id or str(uuid.uuid4()))
        if snapshot.get("spec"):
            snapshot["spec"]["locked_track_ids"] = list(locked_ids)
            if snapshot.get("draft_spec"):
                snapshot["draft_spec"]["locked_track_ids"] = list(locked_ids)
        settings = Settings.load()
        try:
            client = GeminiClient(settings)
        except ModelError as exc:
            result = {"status": "error", "message": str(exc)}
        else:
            try:
                result = await run_agent(
                    message, catalogue, client, snapshot, settings.timeout_s
                )
            finally:
                await client.close()
        store.save(update_snapshot(snapshot, message, result))
        return (*render(snapshot), "")

    def new_session():
        return render(store.load(str(uuid.uuid4())))

    def restore(session_id):
        if not session_id:
            raise gr.Error("Выбери сохранённый диалог.")
        return render(store.load(session_id))

    def save_locks(session_id, selected):
        if not session_id:
            return
        snapshot = store.load(session_id)
        if not set(selected) <= set(snapshot.get("playlist_ids", [])):
            raise gr.Error("Можно закрепить только треки принятого плейлиста.")
        if snapshot.get("spec"):
            snapshot["spec"]["locked_track_ids"] = list(selected)
            if snapshot.get("draft_spec"):
                snapshot["draft_spec"]["locked_track_ids"] = list(selected)
            store.save(snapshot)

    with gr.Blocks(title="Playlist Agent", analytics_enabled=False) as app:
        gr.Markdown(
            "# Playlist Agent\nПодбор музыки с Gemini: реальные треки, проверяемые ограничения и правки в диалоге."
        )
        session_id = gr.State("")
        with gr.Row():
            sessions = gr.Dropdown(
                choices=store.list_sessions(), label="Сохранённый диалог", scale=4
            )
            restore_button = gr.Button("Открыть")
            new_button = gr.Button("Новый диалог")
        with gr.Row():
            with gr.Column(scale=1):
                chat = gr.Chatbot(label="Диалог", height=330)
                message = gr.Textbox(
                    label="Запрос",
                    lines=3,
                    placeholder="50 минут электроники для прогулки: энергичная середина, спокойный конец…",
                )
                submit_button = gr.Button("Собрать / изменить", variant="primary")
                gr.Examples(
                    [
                        "50 минут электроники для прогулки: первые 10 минут спокойно, затем энергичнее, последние 10 минут спокойно. Без повторов исполнителей.",
                        "Подбери 20 инструментальных треков для работы, желательно ambient и chill. Не больше двух треков одного исполнителя.",
                        "Собери 80 электронных треков: спокойное начало, энергичная середина и спокойный финал. Без повторов исполнителей.",
                    ],
                    inputs=message,
                    cache_examples=False,
                )
                spec_view = gr.Markdown()
            with gr.Column(scale=2):
                status = gr.Markdown()
                plot = gr.HTML()
                table = gr.Dataframe(
                    headers=[
                        "№",
                        "Трек",
                        "Исполнители",
                        "Минуты",
                        "Энергия",
                        "Жанры",
                        "Spotify",
                    ],
                    datatype=["number", "str", "str", "number", "number", "str", "str"],
                    interactive=False,
                )
                locks = gr.Dropdown(
                    choices=[], multiselect=True, label="Закреплённые треки"
                )
                files = gr.File(
                    label="Экспорт CSV / JSON", file_count="multiple", interactive=False
                )
                with gr.Accordion("Проверки и измерения", open=False):
                    details = gr.JSON()
        outputs = [
            chat,
            status,
            spec_view,
            table,
            plot,
            locks,
            files,
            details,
            session_id,
            sessions,
        ]
        inputs = [message, session_id, locks]
        submit_button.click(
            submit,
            inputs,
            [*outputs, message],
            concurrency_limit=1,
            concurrency_id="agent",
            api_name="generate",
        )
        message.submit(
            submit,
            inputs,
            [*outputs, message],
            concurrency_limit=1,
            concurrency_id="agent",
            api_name=False,
        )
        locks.input(
            save_locks,
            [session_id, locks],
            api_name=False,
            concurrency_id="agent",
            concurrency_limit=1,
        )
        new_button.click(new_session, outputs=outputs, api_name=False)
        restore_button.click(restore, sessions, outputs, api_name="restore")
        app.load(new_session, outputs=outputs, api_name=False)
    return app.queue(max_size=4, default_concurrency_limit=1)


if __name__ == "__main__":
    create_app().launch(
        server_name=os.environ.get("PLAYLIST_HOST", "127.0.0.1"),
        server_port=int(os.environ.get("PORT", "7860")),
        share=False,
        show_error=False,
    )
