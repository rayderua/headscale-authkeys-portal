// Follow the OS light/dark preference (Bootstrap 5.3 color modes). Runs in <head> to avoid a flash.
(function () {
  var m = window.matchMedia("(prefers-color-scheme: dark)");
  function apply() { document.documentElement.setAttribute("data-bs-theme", m.matches ? "dark" : "light"); }
  apply();
  if (m.addEventListener) m.addEventListener("change", apply);
})();
