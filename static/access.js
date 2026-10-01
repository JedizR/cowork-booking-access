// Access: progressive enhancement only. Every page works without this file.

// E-ticket: the Print button exists only when script can print.
document.querySelectorAll("[data-print]").forEach((button) => {
  button.hidden = false;
  button.addEventListener("click", () => window.print());
});

// Kiosk: a result clears when the next code is typed, or after 15 s,
// so the next guest never reads the previous guest's answer.
const kioskResult = document.querySelector(".kiosk-result");
if (kioskResult) {
  const clear = () => { kioskResult.hidden = true; };
  document.querySelector(".kiosk-input")?.addEventListener("input", clear, { once: true });
  setTimeout(clear, 15000);
}

// Kiosk bar clock: the server renders "Now 09:05"; on real time it keeps ticking here.
const liveClock = document.querySelector("[data-live-clock]");
if (liveClock) {
  const hm = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: "Asia/Bangkok" });
  setInterval(() => { liveClock.textContent = hm.format(new Date()); }, 10000);
}
