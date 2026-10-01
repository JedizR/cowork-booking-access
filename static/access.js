// Access: progressive enhancement only. Every page works without this file.

// E-ticket: the Print button exists only when script can print.
document.querySelectorAll("[data-print]").forEach((button) => {
  button.hidden = false;
  button.addEventListener("click", () => window.print());
});
