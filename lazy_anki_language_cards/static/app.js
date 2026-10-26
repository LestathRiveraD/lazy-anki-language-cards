(() => {
  "use strict";

  const API_HEADERS = { "X-Lazy-Anki-Language-Cards": "1" };
  const CONFIG = window.LAZY_ANKI_LANGUAGE_CARDS || {};
  const SHORTCUT = CONFIG.shortcut || "Ctrl+Z";
  const ANNOTATE_KEY = (CONFIG.annotateShortcut || "A").toLowerCase();

  const listEl = document.getElementById("records");
  const emptyEl = document.getElementById("empty");
  const countEl = document.getElementById("count");
  const statusEl = document.getElementById("status");
  const pauseEl = document.getElementById("pause");
  const undoBtn = document.getElementById("undo");
  const quitBtn = document.getElementById("quit");
  const popEl = document.getElementById("annotate-pop");
  const toastsEl = document.getElementById("toasts");
  const stoppedEl = document.getElementById("stopped");
  const stoppedTextEl = document.getElementById("stopped-text");
  const jsonViewEl = document.getElementById("view-json");
  const recordsViewEl = document.getElementById("view-records");
  const jsonTextEl = document.getElementById("json-text");
  const copyJsonBtn = document.getElementById("copy-json");
  const quitDialog = document.getElementById("quit-dialog");
  const quitCountEl = document.getElementById("quit-count");
  const quitShotsEl = document.getElementById("quit-shots");

  const state = {
    session: null,
    version: -1,
    records: new Map(), // id -> record (from the server)
    cards: new Map(),   // id -> { el, sig }
    latestId: null,     // most recently added record (last in insertion order)
    connected: false,
    stopped: false,
    pending: null,      // selection waiting to be annotated: { id, word }
    view: "records",    // "records" or "json"
    jsonVersion: null,  // dataset version currently shown in the JSON tab
  };

  // ------------------------------------------------------------------ helpers

  async function api(method, url, body) {
    const options = { method, headers: { ...API_HEADERS } };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const response = await fetch(url, options);
    let data = null;
    try { data = await response.json(); } catch (_) { /* empty body */ }
    if (!response.ok) {
      throw new Error((data && data.error) || `${response.status} ${response.statusText}`);
    }
    return data;
  }

  function toast(message, kind = "info", ms = 3500) {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = message;
    toastsEl.appendChild(el);
    setTimeout(() => el.remove(), kind === "error" ? ms * 2 : ms);
  }

  function preview(text, max = 60) {
    const flat = text.replace(/\s+/g, " ").trim();
    return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
  }

  function captureTime(imagePath) {
    const m = imagePath && /(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})(\d{2})/.exec(imagePath);
    if (!m) return "";
    const date = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
    return date.toLocaleString();
  }

  function setStatus() {
    if (state.stopped) { statusEl.dataset.state = "offline"; statusEl.textContent = "Stopped"; return; }
    if (!state.connected) { statusEl.dataset.state = "offline"; statusEl.textContent = "Reconnecting…"; return; }
    if (pauseEl.checked) { statusEl.dataset.state = "paused"; statusEl.textContent = "Capture paused"; return; }
    statusEl.dataset.state = "live";
    statusEl.textContent = "Watching clipboard";
  }

  function isTextEntry(el) {
    if (!el || el === document.body) return false;
    if (el.isContentEditable) return true;
    if (el.tagName === "TEXTAREA" || el.tagName === "SELECT") return true;
    if (el.tagName === "INPUT") {
      return !["checkbox", "radio", "button", "submit", "reset", "range", "color", "file"].includes(el.type);
    }
    return false;
  }

  // ------------------------------------------------------------------ rendering

  let refreshing = null;
  let refreshAgain = false;

  function refresh() {
    if (refreshing) { refreshAgain = true; return refreshing; }
    refreshing = (async () => {
      do {
        refreshAgain = false;
        apply(await api("GET", "/api/records"));
      } while (refreshAgain);
    })()
      .catch((err) => { if (!state.stopped) toast(`Could not load records: ${err.message}`, "error"); })
      .finally(() => { refreshing = null; });
    return refreshing;
  }

  function apply(data) {
    // Ignore responses older than what is already shown (same server process only).
    if (data.session === state.session && data.version < state.version) return;
    state.session = data.session;
    state.version = data.version;
    render(data.records);
    if (state.view === "json") refreshJson();
  }

  function render(records) {
    const ids = new Set(records.map((r) => r.id));
    for (const [id, card] of state.cards) {
      if (!ids.has(id)) { card.el.remove(); state.cards.delete(id); }
    }
    state.records = new Map(records.map((r) => [r.id, r]));
    state.latestId = records.length ? records[records.length - 1].id : null;

    // Newest first; existing cards are reused so an unrelated update never
    // disturbs a selection the user is making in another record.
    let previous = null;
    for (let i = records.length - 1; i >= 0; i--) {
      const record = records[i];
      const sig = JSON.stringify([record.words, record.image_url]);
      let card = state.cards.get(record.id);
      if (!card) {
        card = { el: buildCard(record), sig };
        state.cards.set(record.id, card);
      } else if (card.sig !== sig) {
        fillCard(card.el, record);
        card.sig = sig;
      }
      card.el.classList.toggle("is-latest", record.id === state.latestId);
      const expected = previous ? previous.nextSibling : listEl.firstChild;
      if (expected !== card.el) listEl.insertBefore(card.el, expected);
      previous = card.el;
    }

    emptyEl.hidden = records.length > 0;
    countEl.textContent = `${records.length} record${records.length === 1 ? "" : "s"}`;
    undoBtn.disabled = records.length === 0;
    if (state.pending && !state.records.has(state.pending.id)) hidePopover();
  }

  function buildCard(record) {
    const el = document.createElement("article");
    el.className = "card";
    el.dataset.id = record.id;
    el.innerHTML = `
      <figure class="shot"></figure>
      <div class="content">
        <div class="card-head">
          <span class="meta"></span>
          <button type="button" class="delete" title="Delete this record">Delete</button>
        </div>
        <div class="sentence" data-id="${record.id}"></div>
        <div class="words-label">Annotations</div>
        <ul class="words"></ul>
      </div>`;
    el.querySelector(".meta").textContent = captureTime(record.image_path);
    el.querySelector(".delete").addEventListener("click", () => deleteRecord(record.id));
    fillCard(el, record);
    return el;
  }

  function fillCard(el, record) {
    const shot = el.querySelector(".shot");
    shot.textContent = "";
    if (record.image_url) {
      const link = document.createElement("a");
      link.href = record.image_url;
      link.target = "_blank";
      link.rel = "noopener";
      link.title = "Open full-size screenshot";
      const img = document.createElement("img");
      img.src = record.image_url;
      img.alt = "Screenshot taken when the text was copied";
      img.loading = "lazy";
      img.addEventListener("error", () => {
        shot.innerHTML = '<div class="shot-missing">Screenshot file not found</div>';
      });
      link.appendChild(img);
      shot.appendChild(link);
    } else {
      shot.innerHTML = '<div class="shot-missing">No screenshot</div>';
    }

    renderSentence(el.querySelector(".sentence"), record.sentence, record.words);

    const list = el.querySelector(".words");
    list.textContent = "";
    if (!record.words.length) {
      const li = document.createElement("li");
      li.className = "none";
      li.textContent = "None yet. Select text in the sentence above.";
      list.appendChild(li);
    }
    for (const word of record.words) {
      const li = document.createElement("li");
      li.className = "chip";
      const text = document.createElement("span");
      text.className = "chip-text";
      text.textContent = word;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.title = "Remove this annotation";
      remove.setAttribute("aria-label", `Remove annotation ${word}`);
      remove.textContent = "×";
      remove.addEventListener("click", () => removeWord(record.id, word));
      li.append(text, remove);
      list.appendChild(li);
    }
  }

  // The sentence's DOM text is exactly record.sentence; annotated characters
  // (every occurrence of every word) are wrapped in <mark>.
  function renderSentence(el, sentence, words) {
    const marked = new Uint8Array(sentence.length);
    for (const word of words) {
      if (!word) continue;
      for (let i = sentence.indexOf(word); i !== -1; i = sentence.indexOf(word, i + 1)) {
        marked.fill(1, i, i + word.length);
      }
    }
    el.textContent = "";
    let start = 0;
    for (let i = 1; i <= sentence.length; i++) {
      if (i < sentence.length && marked[i] === marked[start]) continue;
      const text = sentence.slice(start, i);
      if (marked[start]) {
        const mark = document.createElement("mark");
        mark.textContent = text;
        el.appendChild(mark);
      } else {
        el.appendChild(document.createTextNode(text));
      }
      start = i;
    }
  }

  // ------------------------------------------------------------------ selection

  function sentenceOf(node) {
    const el = node && (node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement);
    return el ? el.closest(".sentence") : null;
  }

  // Character offset of a DOM boundary point within the sentence element.
  function offsetIn(el, node, offset) {
    const range = document.createRange();
    range.setStart(el, 0);
    range.setEnd(node, offset);
    return range.toString().length;
  }

  // Map the browser selection to an exact substring of one record's sentence,
  // using character offsets so the stored text is taken from the original string.
  function currentSelection() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
    const range = sel.getRangeAt(0);
    const el = sentenceOf(range.startContainer) || sentenceOf(range.endContainer);
    if (!el) return null;
    const record = state.records.get(Number(el.dataset.id));
    if (!record) return null;
    const sentence = record.sentence;

    let start = el.contains(range.startContainer) ? offsetIn(el, range.startContainer, range.startOffset) : 0;
    let end = el.contains(range.endContainer) ? offsetIn(el, range.endContainer, range.endOffset) : sentence.length;
    // Drop whitespace caught at the edges of a sloppy drag.
    while (start < end && /\s/.test(sentence[start])) start++;
    while (end > start && /\s/.test(sentence[end - 1])) end--;
    if (start >= end) return null;

    const rects = range.getClientRects();
    const rect = rects.length ? rects[rects.length - 1] : range.getBoundingClientRect();
    return { id: record.id, word: sentence.slice(start, end), rect };
  }

  function updatePopover() {
    const info = currentSelection();
    if (!info) { hidePopover(); return; }
    state.pending = { id: info.id, word: info.word };
    const record = state.records.get(info.id);
    const duplicate = record.words.includes(info.word);
    popEl.textContent = duplicate
      ? "Already annotated"
      : `Annotate “${preview(info.word, 40)}”  (${ANNOTATE_KEY.toUpperCase()})`;
    popEl.disabled = duplicate;
    popEl.hidden = false;
    const width = popEl.offsetWidth;
    const left = Math.min(Math.max(8, info.rect.left + window.scrollX), window.scrollX + document.documentElement.clientWidth - width - 8);
    popEl.style.left = `${left}px`;
    popEl.style.top = `${info.rect.bottom + window.scrollY + 8}px`;
  }

  function hidePopover() {
    popEl.hidden = true;
    state.pending = null;
  }

  // Word boundaries around a character offset (Unicode-aware where supported).
  function wordAt(text, offset) {
    if (typeof Intl !== "undefined" && Intl.Segmenter) {
      let previous = null;
      for (const seg of new Intl.Segmenter(undefined, { granularity: "word" }).segment(text)) {
        const end = seg.index + seg.segment.length;
        if (seg.index > offset) break;
        if (!seg.isWordLike) continue;
        if (offset < end) return [seg.index, end];
        if (offset === end) previous = [seg.index, end]; // caret just after a word
      }
      return previous;
    }
    const re = /[\p{L}\p{N}\p{M}_'’-]+/gu;
    for (let m; (m = re.exec(text)); ) {
      if (m.index <= offset && offset <= m.index + m[0].length) return [m.index, m.index + m[0].length];
      if (m.index > offset) break;
    }
    return null;
  }

  // With no selection, the word under the text cursor (after clicking inside a word).
  function caretWord() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0 || !sel.isCollapsed) return null;
    const el = sentenceOf(sel.anchorNode);
    if (!el) return null;
    const record = state.records.get(Number(el.dataset.id));
    if (!record) return null;
    const span = wordAt(record.sentence, offsetIn(el, sel.anchorNode, sel.anchorOffset));
    return span ? { id: record.id, word: record.sentence.slice(span[0], span[1]) } : null;
  }

  let annotating = false;
  async function annotate(target, { announce = false } = {}) {
    if (!target || annotating) return;
    annotating = true;
    popEl.disabled = true;
    try {
      const result = await api("POST", `/api/records/${target.id}/words`, { word: target.word });
      if (!result.added) toast("That text is already annotated.");
      else if (announce) toast(`Annotated “${preview(target.word, 40)}”`);
      window.getSelection().removeAllRanges();
      hidePopover();
      await refresh();
    } catch (err) {
      toast(`Could not annotate: ${err.message}`, "error");
      popEl.disabled = false;
    } finally {
      annotating = false;
    }
  }

  function annotatePending() {
    if (state.pending && !popEl.disabled) annotate(state.pending);
  }

  // Shortcut: annotate the selection, or the word under the cursor if nothing is selected.
  function annotateShortcut() {
    const selected = currentSelection();
    const target = selected ? { id: selected.id, word: selected.word } : caretWord();
    if (!target) {
      toast("Select text in a sentence (or click inside a word) first.");
      return;
    }
    const record = state.records.get(target.id);
    if (record && record.words.includes(target.word)) {
      toast("That text is already annotated.");
      return;
    }
    annotate(target, { announce: true });
  }

  // ------------------------------------------------------------------ actions

  async function deleteRecord(id) {
    try {
      await api("DELETE", `/api/records/${id}`);
      await refresh();
    } catch (err) {
      toast(`Could not delete: ${err.message}`, "error");
      refresh();
    }
  }

  async function removeWord(id, word) {
    try {
      await api("DELETE", `/api/records/${id}/words`, { word });
      await refresh();
    } catch (err) {
      toast(`Could not remove annotation: ${err.message}`, "error");
      refresh();
    }
  }

  let undoInFlight = false;
  async function undoLatest() {
    if (undoInFlight) return;
    undoInFlight = true;
    try {
      // The server removes the newest record in insertion order, so the result
      // never depends on what this page currently shows.
      const result = await api("POST", "/api/undo");
      if (result.removed) toast(`Removed latest record: “${preview(result.removed.sentence)}”`);
      else toast("Nothing to undo: there are no records.");
      await refresh();
    } catch (err) {
      toast(`Undo failed: ${err.message}`, "error");
    } finally {
      undoInFlight = false;
    }
  }

  async function setPaused(paused) {
    try {
      const result = await api("POST", "/api/capture", { paused });
      pauseEl.checked = result.paused;
    } catch (err) {
      pauseEl.checked = !paused;
      toast(`Could not change capture state: ${err.message}`, "error");
    }
    setStatus();
  }

  // ------------------------------------------------------------------ copying

  // Copy text to the system clipboard without it being recorded: the server is
  // told to ignore this exact text *before* it reaches the clipboard.
  async function copyToClipboard(text) {
    try {
      await api("POST", "/api/ignore-copy", { text });
    } catch (_) {
      // App stopped or unreachable: nothing is monitoring, so copying is safe anyway.
    }
    await navigator.clipboard.writeText(text);
  }

  function selectedTextForCopy() {
    const active = document.activeElement;
    if (active && (active.tagName === "TEXTAREA" || active.tagName === "INPUT") && active.selectionStart !== null) {
      return active.value.slice(active.selectionStart, active.selectionEnd);
    }
    const sel = window.getSelection();
    return sel ? sel.toString() : "";
  }

  // Ctrl+C anywhere on this page goes through copyToClipboard as well.
  document.addEventListener("copy", (e) => {
    const text = selectedTextForCopy();
    if (!text) return;
    if (!navigator.clipboard || !window.isSecureContext) {
      // No async clipboard API: let the browser copy, but still ask the server to ignore it.
      api("POST", "/api/ignore-copy", { text }).catch(() => {});
      return;
    }
    e.preventDefault();
    copyToClipboard(text).catch((err) => toast(`Copy failed: ${err.message}`, "error"));
  });

  // ------------------------------------------------------------------ JSON view

  let jsonLoading = null;
  function refreshJson() {
    if (jsonLoading) return jsonLoading;
    jsonLoading = (async () => {
      const response = await fetch("/records.json", { cache: "no-store" });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const version = response.headers.get("X-Dataset-Version");
      const text = await response.text();
      // Keep the scroll position and any selection if nothing changed.
      if (jsonTextEl.value !== text) {
        const top = jsonTextEl.scrollTop;
        jsonTextEl.value = text;
        jsonTextEl.scrollTop = top;
      }
      state.jsonVersion = version;
    })()
      .catch((err) => { if (!state.stopped) toast(`Could not load JSON: ${err.message}`, "error"); })
      .finally(() => {
        jsonLoading = null;
        // Another change may have arrived while loading.
        if (state.view === "json" && state.jsonVersion !== null && Number(state.jsonVersion) < state.version) refreshJson();
      });
    return jsonLoading;
  }

  async function copyJson() {
    try {
      await refreshJson();
      await copyToClipboard(jsonTextEl.value);
      toast(`JSON copied (${state.records.size} record${state.records.size === 1 ? "" : "s"}).`);
    } catch (err) {
      toast(`Copy failed: ${err.message}. Select the text and press Ctrl+C instead.`, "error");
    }
  }

  function showView(view) {
    state.view = view === "json" ? "json" : "records";
    for (const tab of document.querySelectorAll(".tabs [role=tab]")) {
      tab.setAttribute("aria-selected", String(tab.dataset.view === state.view));
    }
    recordsViewEl.hidden = state.view !== "records";
    jsonViewEl.hidden = state.view !== "json";
    hidePopover();
    if (state.view === "json") refreshJson();
    try { localStorage.setItem("lazy-anki-language-cards.view", state.view); } catch (_) { /* storage unavailable */ }
  }

  // ------------------------------------------------------------------ quitting

  function showStopped(discarded) {
    state.stopped = true;
    if (quitDialog.open) quitDialog.close();
    stoppedTextEl.textContent = discarded
      ? "records.json was deleted. You can close this tab."
      : `Your records are saved in ${CONFIG.recordsFile || "records.json"}. You can close this tab.`;
    stoppedEl.hidden = false;
    setStatus();
  }

  function quit() {
    const n = state.records.size;
    quitCountEl.textContent = `${n} record${n === 1 ? "" : "s"}`;
    quitShotsEl.checked = false;
    hidePopover();
    quitDialog.showModal();
  }

  async function confirmQuit(discard) {
    for (const b of quitDialog.querySelectorAll("button")) b.disabled = true;
    try {
      await api("POST", "/api/shutdown", { discard, discard_screenshots: discard && quitShotsEl.checked });
      showStopped(discard);
    } catch (err) {
      toast(`Could not stop: ${err.message}`, "error");
    } finally {
      for (const b of quitDialog.querySelectorAll("button")) b.disabled = false;
    }
  }

  // ------------------------------------------------------------------ events

  popEl.addEventListener("mousedown", (e) => e.preventDefault()); // keep the selection
  popEl.addEventListener("click", annotatePending);

  document.addEventListener("mouseup", (e) => {
    if (e.target === popEl) return;
    setTimeout(updatePopover, 0); // let the browser finish updating the selection
  });
  document.addEventListener("keyup", (e) => {
    if (e.shiftKey || e.key === "Shift" || e.key.startsWith("Arrow")) updatePopover();
  });
  document.addEventListener("selectionchange", () => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) hidePopover();
  });
  window.addEventListener("resize", () => { if (!popEl.hidden) updatePopover(); });

  document.addEventListener("keydown", (e) => {
    if (state.stopped || quitDialog.open || isTextEntry(e.target)) return;
    const key = e.key.toLowerCase();
    if (key === "z" && (e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey) {
      e.preventDefault();
      if (!e.repeat) undoLatest(); // holding the keys must not wipe several records
      return;
    }
    if (key === ANNOTATE_KEY && !e.ctrlKey && !e.metaKey && !e.altKey && state.view === "records") {
      e.preventDefault();
      if (!e.repeat) annotateShortcut();
      return;
    }
    if (e.key === "Enter" && !popEl.hidden && !e.repeat && !(e.target instanceof HTMLButtonElement)) {
      e.preventDefault();
      annotatePending();
    } else if (e.key === "Escape" && !popEl.hidden) {
      window.getSelection().removeAllRanges();
      hidePopover();
    }
  });

  undoBtn.title = `Remove the most recently added record (${SHORTCUT})`;
  undoBtn.addEventListener("click", undoLatest);
  quitBtn.addEventListener("click", quit);
  document.getElementById("quit-keep").addEventListener("click", () => confirmQuit(false));
  document.getElementById("quit-discard").addEventListener("click", () => confirmQuit(true));
  document.getElementById("quit-copy").addEventListener("click", copyJson);
  copyJsonBtn.addEventListener("click", copyJson);
  for (const tab of document.querySelectorAll(".tabs [role=tab]")) {
    tab.addEventListener("click", () => showView(tab.dataset.view));
  }
  pauseEl.addEventListener("change", () => setPaused(pauseEl.checked));

  function connect() {
    const source = new EventSource("/api/events");
    source.addEventListener("open", () => {
      state.connected = true;
      setStatus();
      refresh();
      api("GET", "/api/status").then((s) => { pauseEl.checked = s.paused; setStatus(); }).catch(() => {});
    });
    source.addEventListener("changed", () => refresh());
    source.addEventListener("capture", (e) => { pauseEl.checked = JSON.parse(e.data).paused; setStatus(); });
    source.addEventListener("app-error", (e) => toast(JSON.parse(e.data).message, "error"));
    source.addEventListener("shutdown", (e) => {
      source.close();
      showStopped(Boolean(JSON.parse(e.data).discarded));
    });
    source.addEventListener("error", () => {
      if (state.stopped) { source.close(); return; }
      state.connected = false;
      setStatus();
    });
  }

  let savedView = "records";
  try { savedView = localStorage.getItem("lazy-anki-language-cards.view") || "records"; } catch (_) { /* storage unavailable */ }
  showView(location.hash === "#json" ? "json" : savedView);
  setStatus();
  refresh();
  connect();
})();
