// Copy buttons and countdowns. No inline scripts (strict CSP).
(function () {
  function copy(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var ta = document.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy") ? resolve() : reject(); } catch (e) { reject(e); }
      document.body.removeChild(ta);
    });
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-copy]");
    if (!b) return;
    var label = b.textContent;
    copy(b.getAttribute("data-copy")).then(function () {
      b.textContent = "✓"; setTimeout(function () { b.textContent = label; }, 1200);
    });
  });
  function tick() {
    document.querySelectorAll(".countdown").forEach(function (el) {
      var left = Math.max(0, Math.floor((new Date(el.dataset.expires) - Date.now()) / 1000));
      el.textContent = Math.floor(left / 60) + ":" + String(left % 60).padStart(2, "0");
    });
  }
  tick(); setInterval(tick, 1000);
})();
