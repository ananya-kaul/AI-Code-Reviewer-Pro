/* Light UI enhancements. Core navigation and review actions work without JS. */
(function () {
  "use strict";

  /* ---- Animated count-up + ring fill for the score gauge ---- */
  function animateScore(ring) {
    try {
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
      var numEl = ring.querySelector(".score-num");
      var raw = ring.getAttribute("data-score") || "";
      var target = parseFloat(raw);
      if (isNaN(target)) return;
      if (ring.__lastScore === target) return;
      ring.__lastScore = target;

      var start = null;
      var duration = 950;
      function frame(ts) {
        if (start === null) start = ts;
        var p = Math.min((ts - start) / duration, 1);
        var eased = 1 - Math.pow(1 - p, 3); // easeOutCubic
        if (numEl) numEl.textContent = (target * eased).toFixed(1);
        ring.style.setProperty("--pct", (target / 10) * 100 * eased + "%");
        if (p < 1) requestAnimationFrame(frame);
      }
      // Begin the fill from zero for a satisfying sweep.
      ring.style.setProperty("--pct", "0%");
      requestAnimationFrame(frame);
    } catch (_) {}
  }

  /* ---- Observe the DOM and (re)wire elements as Gradio renders ---- */
  function scan() {
    document.querySelectorAll(".score-ring[data-score]").forEach(function (r) {
      if (r.getAttribute("data-score") !== "—") animateScore(r);
    });
    // Nav active-state toggle (view switch is handled server-side).
    document.querySelectorAll(".topnav__link").forEach(function (link) {
      if (link.__navBound) return;
      link.__navBound = true;
      link.addEventListener("click", function () {
        document
          .querySelectorAll(".topnav__link")
          .forEach(function (l) { l.classList.remove("is-active"); });
        link.classList.add("is-active");
      });
    });
    document.querySelectorAll(".reopen-btn").forEach(function (button) {
      if (button.__navBound) return;
      button.__navBound = true;
      button.addEventListener("click", function () {
        document.querySelectorAll(".topnav__link").forEach(function (link) {
          link.classList.toggle("is-active", link.textContent.trim() === "Dashboard");
        });
      });
    });
  }

  function boot() {
    scan();
    try {
      var mo = new MutationObserver(function () {
        window.requestAnimationFrame(scan);
      });
      mo.observe(document.body, { childList: true, subtree: true, characterData: true });
    } catch (_) {}
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
