const $ = (id) => document.getElementById(id);

let sessionId = null;
let pending = null;
let busy = false;

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(
      data.message || data.detail || "The demo could not complete this request."
    );
  }
  return {data, status: response.status};
}

async function refreshSessions() {
  const {data} = await api("/api/sessions");
  const select = $("sessions");
  select.replaceChildren(new Option("New conversation", ""));
  for (const item of data) {
    select.add(new Option(item.label || "Conversation", item.id));
  }
  select.value = sessionId || "";
}

function render(data) {
  sessionId = data.id;
  $("chat").replaceChildren();
  for (const entry of data.messages) {
    const paragraph = document.createElement("p");
    paragraph.className = entry.role === "user" ? "user" : "assistant";
    paragraph.textContent = entry.content;
    $("chat").append(paragraph);
  }

  $("status").textContent = data.message;
  $("interpretation").textContent = data.interpretation;

  $("chart").replaceChildren();
  if (data.chart) {
    const image = document.createElement("img");
    image.alt = "Track energy and requested target over playback time";
    image.src =
      "data:image/svg+xml;charset=utf-8," + encodeURIComponent(data.chart);
    $("chart").append(image);
  }

  const metrics = data.metrics;
  if (metrics.elapsed_s == null) {
    $("metrics").textContent = "";
  } else {
    const tokens =
      metrics.total_tokens == null ? "unknown" : metrics.total_tokens;
    $("metrics").textContent =
      `${metrics.elapsed_s}s · ${tokens} reported tokens`;
  }

  $("tracks").replaceChildren();
  data.tracks.forEach((track, index) => {
    const row = document.createElement("tr");

    const cell = document.createElement("td");
    const lock = document.createElement("input");
    lock.type = "checkbox";
    lock.value = track.id;
    lock.checked = data.locked_ids.includes(track.id);
    lock.setAttribute("aria-label", `Keep ${track.title}`);
    cell.append(lock);
    row.append(cell);

    const values = [
      index + 1,
      track.title,
      track.artists,
      track.minutes,
      track.energy == null ? "—" : track.energy,
    ];
    for (const value of values) {
      const td = document.createElement("td");
      td.textContent = value;
      row.append(td);
    }

    const linkCell = document.createElement("td");
    const link = document.createElement("a");
    link.href = "https://open.spotify.com/track/" + encodeURIComponent(track.id);
    link.textContent = "Open";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    linkCell.append(link);
    row.append(linkCell);

    $("tracks").append(row);
  });

  $("exports").hidden = !data.tracks.length;
  for (const format of ["csv", "json"]) {
    $(format).href =
      `/api/sessions/${encodeURIComponent(sessionId)}/export/${format}`;
    $(format).download = `playlist.${format}`;
  }
}

function toggleBusy(value) {
  busy = value;
  for (const id of ["build", "open", "new", "sessions"]) {
    $(id).disabled = value;
  }
  $("request").readOnly = value;
  for (const element of document.querySelectorAll("#tracks input")) {
    element.disabled = value;
  }
  $("progress").textContent = value
    ? "Building your playlist. This may take up to three minutes…"
    : "";
}

$("generate").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;

  const message = $("request").value.trim();
  const lockedIds = Array.from(
    document.querySelectorAll("#tracks input:checked"),
    (element) => element.value
  );
  const content = JSON.stringify({
    message,
    session_id: sessionId,
    locked_ids: lockedIds,
  });

  // A lost HTTP response must reuse its request ID, not spend another run.
  if (!pending || pending.content !== content) {
    pending = {content, id: crypto.randomUUID()};
  }

  toggleBusy(true);
  try {
    const {data, status} = await api("/api/generate", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Demo-Request": "1",
      },
      body: JSON.stringify({
        ...JSON.parse(content),
        request_id: pending.id,
      }),
    });
    if (status === 202) {
      $("status").textContent =
        data.message +
        " Use Open to check the saved conversation; submitting again will reuse this request ID.";
      // Keep the original content/session-null payload for an idempotent retry.
    } else {
      render(data);
      pending = null;
      if (data.status !== "error") $("request").value = "";
    }
    await refreshSessions();
  } catch (error) {
    $("status").textContent = error.message;
  } finally {
    toggleBusy(false);
  }
});

$("open").addEventListener("click", async () => {
  const id = $("sessions").value;
  if (!id || busy) return;
  try {
    const {data} = await api(`/api/sessions/${encodeURIComponent(id)}`);
    render(data);
    pending = null;
  } catch (error) {
    $("status").textContent = error.message;
  }
});

$("new").addEventListener("click", () => {
  if (busy) return;
  sessionId = null;
  pending = null;
  $("sessions").value = "";
  $("chat").replaceChildren();
  $("tracks").replaceChildren();
  $("chart").replaceChildren();
  $("interpretation").textContent = "";
  $("metrics").textContent = "";
  $("exports").hidden = true;
  $("status").textContent =
    "Describe a duration or track count and your musical preferences.";
});

function showOwnerAccess() {
  $("owner-access").hidden = location.hash !== "#owner";
}

window.addEventListener("hashchange", showOwnerAccess);
showOwnerAccess();

$("owner-access").addEventListener("submit", async (event) => {
  event.preventDefault();
  const keyInput = $("owner-key");
  const body = JSON.stringify({key: keyInput.value});
  keyInput.value = "";
  try {
    const {data} = await api("/owner-access", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Demo-Request": "1",
      },
      body,
    });
    $("owner-status").textContent = data.message;
  } catch (error) {
    $("owner-status").textContent = error.message;
  }
});

refreshSessions().catch((error) => {
  $("status").textContent = error.message;
});
