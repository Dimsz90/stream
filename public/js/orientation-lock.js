(function (global) {
  const orientation = global.screen && global.screen.orientation;

  async function lock(value) {
    let success = false;
    // 1. Try Capacitor ScreenOrientation plugin
    try {
      if (global.Capacitor?.Plugins?.ScreenOrientation) {
        await global.Capacitor.Plugins.ScreenOrientation.lock({ orientation: value });
        success = true;
      }
    } catch (e) {}

    // 2. Try Web API ScreenOrientation
    if (!success && orientation?.lock) {
      try {
        await orientation.lock(value);
        success = true;
      } catch (e) {}
    }

    // Handle StatusBar hide on landscape / show on portrait
    try {
      if (global.Capacitor?.Plugins?.StatusBar) {
        if (value.includes('landscape')) {
          await global.Capacitor.Plugins.StatusBar.hide();
        } else {
          await global.Capacitor.Plugins.StatusBar.show();
        }
      }
    } catch (e) {}

    return success;
  }

  async function unlock() {
    try {
      if (global.Capacitor?.Plugins?.ScreenOrientation) {
        await global.Capacitor.Plugins.ScreenOrientation.unlock();
      }
    } catch (e) {}

    try {
      if (orientation?.unlock) orientation.unlock();
    } catch (e) {}

    try {
      if (global.Capacitor?.Plugins?.StatusBar) {
        await global.Capacitor.Plugins.StatusBar.show();
      }
    } catch (e) {}
  }

  global.OrientationLock = {
    lock,
    unlock,
    lockPortrait: () => lock("portrait"),
    lockLandscape: () => lock("landscape"),
    supported: () => true,
  };
})(window);
