// Applies the saved theme before first paint, to avoid a flash. Loaded as a normal (blocking) script.
try {
  var t = localStorage.getItem("sh-theme");
  if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
} catch (e) {}
