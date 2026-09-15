(function () {
  "use strict";

  var suppressCardClicks = false;
  var lastCardTrigger = null;
  var lastLightboxTrigger = null;
  var lightboxItems = [];
  var lightboxIndex = 0;

  function announce(message) {
    var output = document.querySelector("#app-announcer");
    if (!output) return;
    output.textContent = "";
    window.setTimeout(function () {
      output.textContent = message;
    }, 20);
  }

  function processBoardReplacement(board, html) {
    var wrapper = document.createElement("div");
    wrapper.innerHTML = html;
    var replacement = wrapper.querySelector("#board-columns");
    if (!replacement) throw new Error("Board response was incomplete");
    board.replaceWith(replacement);
    if (window.htmx) window.htmx.process(replacement);
    initializeSortables();
  }

  async function refreshBoard(message) {
    var board = document.querySelector("#board-columns");
    if (!board) return;
    var response = await fetch("/board", {
      credentials: "same-origin",
      headers: { "HX-Request": "true" },
    });
    var redirect = response.headers.get("HX-Redirect");
    if (redirect) {
      window.location.assign(redirect);
      return;
    }
    if (!response.ok) throw new Error("Board refresh failed");
    processBoardReplacement(board, await response.text());
    if (message) announce(message);
  }

  function initializeSortables() {
    if (!window.Sortable) return;
    document.querySelectorAll(".card-list").forEach(function (list) {
      if (list.dataset.sortableReady === "true") return;
      list.dataset.sortableReady = "true";
      window.Sortable.create(list, {
        group: "trubby-board",
        animation: 150,
        draggable: ".card-tile",
        filter: ".move-form, select, button, input, textarea, [data-no-drag]",
        preventOnFilter: false,
        forceFallback: true,
        fallbackOnBody: true,
        fallbackTolerance: 5,
        delay: 110,
        delayOnTouchOnly: true,
        touchStartThreshold: 4,
        ghostClass: "card-ghost",
        chosenClass: "card-chosen",
        dragClass: "card-dragging",
        fallbackClass: "card-fallback",
        onStart: function () {
          suppressCardClicks = true;
          document.body.classList.add("board-is-dragging");
        },
        onEnd: function (event) {
          document.body.classList.remove("board-is-dragging");
          window.setTimeout(function () {
            suppressCardClicks = false;
          }, 250);
          moveCard(event);
        },
      });
    });
  }

  async function moveCard(event) {
    var card = event.item;
    var board = document.querySelector("#board-columns");
    var status = event.to.dataset.status;
    var cardId = card.dataset.cardId;
    if (!board || !status || !cardId) return;

    try {
      var response = await fetch("/cards/" + cardId + "/move", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "HX-Request": "true",
          "X-CSRF-Token": board.dataset.csrf,
        },
        body: new URLSearchParams({
          status: status,
          position: String(event.newDraggableIndex),
          view: "board",
        }),
      });
      var redirect = response.headers.get("HX-Redirect");
      if (redirect) {
        window.location.assign(redirect);
        return;
      }
      if (!response.ok) throw new Error("Move failed");
      processBoardReplacement(board, await response.text());
      announce("Card moved to " + status.replace("todo", "To do") + ".");
    } catch (_error) {
      try {
        await refreshBoard("That card did not move. The board has been restored.");
      } catch (_refreshError) {
        window.location.reload();
      }
    }
  }

  function syncCardModal() {
    var drawer = document.querySelector("#card-drawer");
    var wasOpen = document.body.classList.contains("card-modal-open");
    document.body.classList.toggle("card-modal-open", Boolean(drawer));

    if (drawer && drawer.dataset.focused !== "true") {
      drawer.dataset.focused = "true";
      drawer.focus({ preventScroll: true });
    } else if (!drawer && wasOpen && lastCardTrigger && lastCardTrigger.isConnected) {
      lastCardTrigger.focus({ preventScroll: true });
    }
  }

  function closeDrawer() {
    var close = document.querySelector("#card-drawer .drawer-close");
    if (close) close.click();
  }

  function openCard(cardId) {
    var path = "/cards/" + cardId;
    lastCardTrigger = document.querySelector('.card-tile[data-card-id="' + cardId + '"] .card-link');
    if (!window.htmx) {
      window.location.assign(path);
      return;
    }
    window.htmx
      .ajax("GET", path, {
        target: "#card-drawer-container",
        swap: "innerHTML",
      })
      .then(function () {
        window.history.pushState({}, "", path);
        syncCardModal();
      })
      .catch(function () {
        window.location.assign(path);
      });
  }

  function updateFileLabel(input) {
    var output = input.closest("form").querySelector(".selected-files");
    if (!output) return;
    if (!input.files.length) {
      output.textContent = "";
    } else if (input.files.length === 1) {
      output.textContent = input.files[0].name;
    } else {
      output.textContent = input.files.length + " files selected";
    }
  }

  function lightbox() {
    return document.querySelector("#image-lightbox");
  }

  function lightboxIsOpen() {
    var element = lightbox();
    return Boolean(element && !element.hidden);
  }

  function renderLightbox() {
    var element = lightbox();
    var item = lightboxItems[lightboxIndex];
    if (!element || !item) return;

    var filename = item.dataset.filename || "Image";
    var image = element.querySelector("#lightbox-image");
    image.src = item.dataset.previewSrc;
    image.alt = filename;
    element.querySelector("#lightbox-filename").textContent = filename;
    element.querySelector("#lightbox-count").textContent =
      lightboxItems.length > 1 ? lightboxIndex + 1 + " of " + lightboxItems.length : "";
    element.querySelector("#lightbox-download").href = item.dataset.downloadSrc;
    element.querySelector("[data-lightbox-previous]").hidden = lightboxItems.length < 2;
    element.querySelector("[data-lightbox-next]").hidden = lightboxItems.length < 2;
  }

  function openLightbox(trigger) {
    var element = lightbox();
    var drawer = document.querySelector("#card-drawer");
    if (!element || !drawer) return;
    lightboxItems = Array.from(drawer.querySelectorAll("[data-lightbox]"));
    lightboxIndex = lightboxItems.indexOf(trigger);
    if (lightboxIndex < 0) return;

    lastLightboxTrigger = trigger;
    renderLightbox();
    element.hidden = false;
    document.body.classList.add("lightbox-open");
    element.querySelector(".lightbox-close").focus({ preventScroll: true });
  }

  function closeLightbox() {
    var element = lightbox();
    if (!element || element.hidden) return;
    element.hidden = true;
    document.body.classList.remove("lightbox-open");
    element.querySelector("#lightbox-image").src = "";
    if (lastLightboxTrigger && lastLightboxTrigger.isConnected) {
      lastLightboxTrigger.focus({ preventScroll: true });
    }
  }

  function navigateLightbox(direction) {
    if (lightboxItems.length < 2) return;
    lightboxIndex = (lightboxIndex + direction + lightboxItems.length) % lightboxItems.length;
    renderLightbox();
  }

  function trapFocus(container, event) {
    var focusable = Array.from(
      container.querySelectorAll(
        'a[href], button:not([disabled]):not([hidden]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )
    ).filter(function (element) {
      return element.offsetParent !== null;
    });
    if (!focusable.length) return;
    var first = focusable[0];
    var last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  document.addEventListener(
    "click",
    function (event) {
      if (suppressCardClicks && event.target.closest(".card-link")) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    },
    true
  );

  document.addEventListener("pointerdown", function (event) {
    var link = event.target.closest(".card-link");
    if (link) lastCardTrigger = link;
  });

  document.addEventListener("DOMContentLoaded", function () {
    initializeSortables();
    syncCardModal();
  });

  document.addEventListener("htmx:afterSwap", function () {
    initializeSortables();
    syncCardModal();
  });

  document.addEventListener("trubby:card-created", function (event) {
    var cardId = event.detail && event.detail.id;
    if (cardId) window.setTimeout(function () { openCard(cardId); }, 0);
  });

  document.addEventListener("htmx:afterRequest", function (event) {
    if (!event.detail.successful) return;
    var form = event.detail.elt;
    if (form && form.classList && form.classList.contains("quick-add")) {
      form.reset();
    }
  });

  document.addEventListener("change", function (event) {
    if (event.target.matches(".file-picker input[type=file]")) {
      updateFileLabel(event.target);
    }
  });

  document.addEventListener("click", function (event) {
    var preview = event.target.closest("[data-lightbox]");
    if (preview) {
      event.preventDefault();
      openLightbox(preview);
      return;
    }
    if (event.target.closest("[data-lightbox-close]")) {
      closeLightbox();
      return;
    }
    if (event.target.closest("[data-lightbox-previous]")) {
      navigateLightbox(-1);
      return;
    }
    if (event.target.closest("[data-lightbox-next]")) {
      navigateLightbox(1);
      return;
    }
    if (event.target.matches("[data-drawer-close]")) closeDrawer();
  });

  document.addEventListener("keydown", function (event) {
    if (lightboxIsOpen()) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeLightbox();
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        navigateLightbox(-1);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        navigateLightbox(1);
      } else if (event.key === "Tab") {
        trapFocus(lightbox(), event);
      }
      return;
    }

    var drawer = document.querySelector("#card-drawer");
    if (event.key === "Escape" && drawer) {
      event.preventDefault();
      closeDrawer();
    } else if (event.key === "Tab" && drawer) {
      trapFocus(drawer, event);
    }
  });
})();
