function toggleTheme() {
  const html = document.documentElement;
  html.dataset.theme = html.dataset.theme === "1" ? "2" : "1";
}
