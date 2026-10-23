// Drag and drop for the BD Pipeline board.
// Cards carry data-company, columns carry data-stage. On drop we hand the move to Dash
// through the "pipeline-move" store; the Python callback saves it and re-renders the board.
(function () {
  function column(el) { return el && el.closest ? el.closest("[data-stage]") : null; }

  document.addEventListener("dragstart", function (e) {
    var card = e.target.closest && e.target.closest("[data-company]");
    if (!card) return;
    e.dataTransfer.setData("text/plain", card.dataset.company);
    e.dataTransfer.effectAllowed = "move";
    card.classList.add("dragging");
  });

  document.addEventListener("dragend", function (e) {
    var card = e.target.closest && e.target.closest("[data-company]");
    if (card) card.classList.remove("dragging");
    document.querySelectorAll(".kcol.over").forEach(function (c) { c.classList.remove("over"); });
  });

  document.addEventListener("dragover", function (e) {
    var col = column(e.target);
    if (!col) return;
    e.preventDefault();  // allow dropping
    e.dataTransfer.dropEffect = "move";
    col.classList.add("over");
  });

  document.addEventListener("dragleave", function (e) {
    var col = column(e.target);
    if (col && !col.contains(e.relatedTarget)) col.classList.remove("over");
  });

  document.addEventListener("drop", function (e) {
    var col = column(e.target);
    if (!col) return;
    e.preventDefault();
    col.classList.remove("over");
    var company = e.dataTransfer.getData("text/plain");
    if (!company || !window.dash_clientside) return;
    window.dash_clientside.set_props("pipeline-move", {
      data: { company: company, stage: col.dataset.stage, t: Date.now() },
    });
  });
})();
