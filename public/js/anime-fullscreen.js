(function () {
  "use strict";

  function animePlayerIsOpen() {
    return document.getElementById("animePlayerModal")?.style.display === "flex";
  }

  function isFullscreenActive() {
    const video = document.getElementById("animeVideo");
    return !!(
      document.fullscreenElement ||
      document.webkitFullscreenElement ||
      document.mozFullScreenElement ||
      (video && video.webkitDisplayingFullscreen)
    );
  }

  async function handleFullscreenChange() {
    if (!animePlayerIsOpen()) return;
    if (isFullscreenActive()) {
      await window.OrientationLock?.lockLandscape?.();
    } else {
      await window.OrientationLock?.unlock?.();
    }
  }

  document.addEventListener("fullscreenchange", handleFullscreenChange);
  document.addEventListener("webkitfullscreenchange", handleFullscreenChange);

  // Hook directly on video element
  function hookVideoFullscreen() {
    const video = document.getElementById("animeVideo");
    if (video && !video.dataset.fsHooked) {
      video.dataset.fsHooked = "1";
      video.addEventListener("webkitbeginfullscreen", () => window.OrientationLock?.lockLandscape?.());
      video.addEventListener("webkitendfullscreen", () => window.OrientationLock?.unlock?.());
      video.addEventListener("play", () => {
        // Prepare orientation lock listener
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", hookVideoFullscreen);
  } else {
    hookVideoFullscreen();
  }
})();
