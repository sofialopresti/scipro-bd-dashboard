// Outreach Assistant: Enter sends (Shift+Enter = new line) and the chat auto-scrolls.
(function () {
  document.addEventListener("keydown", function (e) {
    if (e.target.id !== "chat-input" || e.key !== "Enter" || e.shiftKey || e.isComposing) return;
    e.preventDefault();
    var send = document.getElementById("chat-send");
    if (send && !send.disabled && e.target.value.trim()) send.click();
  });

  // scroll to the newest message whenever the conversation or typing indicator changes
  var observer = new MutationObserver(function () {
    var box = document.getElementById("chat-scroll");
    if (box) box.scrollTop = box.scrollHeight;
  });
  var timer = setInterval(function () {
    var box = document.getElementById("chat-scroll");
    if (!box) return;
    clearInterval(timer);
    observer.observe(box, { childList: true, subtree: true, attributes: true,
                            attributeFilter: ["style"] });
  }, 300);
})();
