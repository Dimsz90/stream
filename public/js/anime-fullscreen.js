(function () {
  "use strict";

  function animePlayerIsOpen() {
    return document.getElementById("animePlayerModal")?.style.display === "flex";
  }

  function documentIsFullscreen() {
    return !!(
      document.fullscreenElement ||
      document.webkitFullscreenElement ||
      document.mozFullScreenElement
    );
  }

  async function keepAnimeFullscreenLandscape() {
    if (!animePlayerIsOpen() || !documentIsFullscreen()) return;
    await window.OrientationLock?.lockLandscape?.();
  }

  document.addEventListener("fullscreenchange", keepAnimeFullscreenLandscape);
  document.addEventListener("webkitfullscreenchange", keepAnimeFullscreenLandscape);
})();
