"use strict";
const form = document.getElementById("portal-application");
const views = {
  landing: "landing",
  application: "application-view",
  review: "review-view",
  confirmation: "confirmation-view",
};
const fields = [
  ["full_name", "Full name"],
  ["email", "Email address"],
  ["phone", "Phone number"],
  ["pan", "PAN"],
  ["address", "Address"],
  ["statement_total", "Statement total (INR)"],
];
function showView(view) {
  Object.entries(views).forEach(([key, id]) => {
    document.getElementById(id).hidden = key !== view;
  });
  document.querySelectorAll("[data-step]").forEach((item) => {
    if (item.dataset.step === view) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  });
  document.title =
    {
      landing: "Start",
      application: "Application details",
      review: "Review before submission",
      confirmation: "Synthetic confirmation",
    }[view] + " · Meridian demo";
  window.scrollTo({ top: 0, behavior: "instant" });
  const title = document.getElementById(views[view]).querySelector("h1");
  title.setAttribute("tabindex", "-1");
  title.focus({ preventScroll: true });
}
document
  .getElementById("start-application")
  .addEventListener("click", () => showView("application"));
document
  .getElementById("back-to-start")
  .addEventListener("click", () => showView("landing"));
form.addEventListener("submit", (event) => event.preventDefault());
document.getElementById("review-application").addEventListener("click", () => {
  const status = document.getElementById("form-status");
  if (!form.reportValidity()) {
    status.hidden = false;
    status.textContent =
      "Complete the required details before continuing. You can ask the user for missing information or a statement.";
    return;
  }
  status.hidden = true;
  const list = document.getElementById("review-details");
  list.replaceChildren();
  fields.forEach(([id, label]) => {
    const row = document.createElement("div"),
      term = document.createElement("dt"),
      value = document.createElement("dd");
    term.textContent = label;
    value.textContent = document.getElementById(id).value;
    row.append(term, value);
    list.append(row);
  });
  showView("review");
});
document
  .getElementById("edit-application")
  .addEventListener("click", () => showView("application"));
document.getElementById("submit-application").addEventListener("click", () => {
  if (form.checkValidity()) showView("confirmation");
  else showView("application");
});
document.getElementById("reset-application").addEventListener("click", () => {
  form.reset();
  document.getElementById("review-details").replaceChildren();
  document.getElementById("form-status").hidden = true;
  showView("landing");
});
