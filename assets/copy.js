// Copy buttons on the Outreach page. Each approved email's button carries its text in
// data-copy; "Copy all approved" joins every approved email on the page.
(function () {
  function flash(btn, text) {
    var old = btn.textContent;
    btn.textContent = text;
    setTimeout(function () { btn.textContent = old; }, 1500);
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest(".copy-btn, #copy-all");
    if (!btn) return;
    var text = btn.id === "copy-all"
      ? Array.from(document.querySelectorAll(".outreach-card .copy-btn"))
          .map(function (b) { return b.dataset.copy; }).join("\n\n----------\n\n")
      : btn.dataset.copy;
    if (!text) { flash(btn, "Nothing approved yet"); return; }
    navigator.clipboard.writeText(text).then(
      function () { flash(btn, "Copied!"); },
      function () { flash(btn, "Copy failed"); }
    );
  });
})();
