(function () {
  const IDS = [
    "team-filter",
    "position-filter",
    "opponent-filter",
    "round-filter",
    "xaxis-group",
  ];

  const COLORS = {
    controlBg: "rgba(8,24,64,0.96)",
    controlBorder: "rgba(158,200,255,0.52)",
    text: "#f4f8ff",
    placeholder: "#c8d8f4",
    chipBg: "rgba(31,111,229,0.26)",
    chipBorder: "rgba(158,200,255,0.48)",
  };

  function apply(root) {
    if (!root) return;

    const controls = root.querySelectorAll(
      ".Select-control, .Select__control, [class*='-control']"
    );
    controls.forEach((el) => {
      el.style.backgroundColor = COLORS.controlBg;
      el.style.color = COLORS.text;
      el.style.borderColor = COLORS.controlBorder;
    });

    const controlDescendants = root.querySelectorAll(
      ".Select-control *, .Select__control *, [class*='-control'] *"
    );
    controlDescendants.forEach((el) => {
      el.style.color = COLORS.text;
      el.style.webkitTextFillColor = COLORS.text;
      el.style.opacity = "1";
    });

    const placeholders = root.querySelectorAll(
      ".Select-placeholder, .Select__placeholder, [class*='-placeholder']"
    );
    placeholders.forEach((el) => {
      el.style.color = COLORS.placeholder;
      el.style.opacity = "1";
    });

    const values = root.querySelectorAll(
      ".Select-value-label, .Select__single-value, [class*='-singleValue']"
    );
    values.forEach((el) => {
      el.style.color = COLORS.text;
    });

    const inputs = root.querySelectorAll(
      ".Select-input > input, .Select__input input, [class*='-input'] input"
    );
    inputs.forEach((el) => {
      el.style.color = COLORS.text;
      el.style.backgroundColor = "transparent";
    });

    const chips = root.querySelectorAll(
      ".Select--multi .Select-value, .Select__multi-value, [class*='-multiValue']"
    );
    chips.forEach((el) => {
      el.style.backgroundColor = COLORS.chipBg;
      el.style.border = `1px solid ${COLORS.chipBorder}`;
    });

    const valueContainers = root.querySelectorAll(
      ".Select-multi-value-wrapper, .Select__value-container, [class*='valueContainer']"
    );
    valueContainers.forEach((el) => {
      el.style.color = COLORS.text;
      el.style.webkitTextFillColor = COLORS.text;
      el.style.opacity = "1";
    });

    const summaryLabels = root.querySelectorAll(
      ".Select-value-label, .Select-multi-value-wrapper span, [class*='multiValueLabel']"
    );
    summaryLabels.forEach((el) => {
      el.style.color = COLORS.text;
      el.style.webkitTextFillColor = COLORS.text;
      el.style.opacity = "1";
    });
  }

  function applyAll() {
    IDS.forEach((id) => apply(document.getElementById(id)));
  }

  function initObserver() {
    const observer = new MutationObserver(() => applyAll());
    observer.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      applyAll();
      initObserver();
      setTimeout(applyAll, 100);
      setTimeout(applyAll, 400);
    });
  } else {
    applyAll();
    initObserver();
    setTimeout(applyAll, 100);
    setTimeout(applyAll, 400);
  }
})();
