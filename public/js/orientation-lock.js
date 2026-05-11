(function (global) {
  const orientation = global.screen && global.screen.orientation;

  async function lock(value) {
    try {
      if (orientation?.lock) {
        await orientation.lock(value);
        return true;
      }
    } catch (e) {
      console.warn("Orientation lock gagal:", e.message);
    }
    return false;
  }

  function unlock() {
    try {
      if (orientation?.unlock) orientation.unlock();
      return true;
    } catch {
      return false;
    }
  }

  global.OrientationLock = {
    lock,
    unlock,
    lockPortrait: () => lock("portrait"),
    lockLandscape: () => lock("landscape"),
    supported: () => !!orientation?.lock,
  };
})(window);
