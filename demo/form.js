"use strict";
// This fixture deliberately performs no fetch, navigation, storage, or network submission.
document.getElementById("application").addEventListener("submit", (event) => {
  event.preventDefault();
  document.getElementById("application").hidden = true;
  document.getElementById("confirmation").hidden = false;
  document.title = "Application received locally · Demo complete";
});
document.getElementById("start-again").addEventListener("click", () => {
  document.getElementById("application").reset();
  document.getElementById("application").hidden = false;
  document.getElementById("confirmation").hidden = true;
  document.title = "Local application · Dev Privacy Guard demo";
});
