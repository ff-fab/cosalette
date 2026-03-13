/* Mermaid diagram zoom — click to fullscreen overlay ---------------------- */

document.addEventListener("DOMContentLoaded", function () {
  "use strict";

  /** Create the reusable overlay element (once). */
  function createOverlay() {
    var overlay = document.createElement("div");
    overlay.className = "mermaid-zoom-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-label", "Zoomed diagram — click or press Escape to close");

    var hint = document.createElement("span");
    hint.className = "mermaid-zoom-close";
    hint.textContent = "Click or press Esc to close";
    overlay.appendChild(hint);

    overlay.addEventListener("click", function () { closeOverlay(overlay); });
    return overlay;
  }

  /** Open the overlay with a clone of the given SVG. */
  function openOverlay(overlay, svg) {
    // Remove any previous SVG clone
    var prev = overlay.querySelector("svg");
    if (prev) prev.remove();

    var clone = svg.cloneNode(true);
    // Remove fixed dimensions so CSS max-width/max-height take effect
    clone.removeAttribute("width");
    clone.removeAttribute("height");
    clone.style.width = "";
    clone.style.height = "";
    overlay.appendChild(clone);

    document.body.appendChild(overlay);
    // Force reflow before adding class for transition
    void overlay.offsetWidth;
    overlay.classList.add("active");
    document.body.style.overflow = "hidden";
  }

  /** Close the overlay. */
  function closeOverlay(overlay) {
    overlay.classList.remove("active");
    document.body.style.overflow = "";
    overlay.addEventListener("transitionend", function handler() {
      overlay.removeEventListener("transitionend", handler);
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    });
  }

  /** Attach click handlers to all Mermaid diagrams on the page. */
  function attachZoom() {
    var overlay = createOverlay();
    var diagrams = document.querySelectorAll(".mermaid");

    diagrams.forEach(function (el) {
      el.addEventListener("click", function (e) {
        var svg = el.querySelector("svg");
        if (!svg) return;
        e.stopPropagation();
        openOverlay(overlay, svg);
      });
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && overlay.classList.contains("active")) {
        closeOverlay(overlay);
      }
    });
  }

  // Mermaid renders asynchronously — wait for SVGs to appear, then attach.
  var attempts = 0;
  var timer = setInterval(function () {
    attempts++;
    if (document.querySelector(".mermaid svg") || attempts > 40) {
      clearInterval(timer);
      attachZoom();
    }
  }, 250);
});
