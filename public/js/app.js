/* ═══════════════════════════════════════
   CONFIG
═══════════════════════════════════════ */
const IMDB_API   = "/api/imdb-proxy";
const STREAM_API = location.protocol.startsWith("http") ? location.origin : "";
const STREAM_PROXY_FALLBACK = "";
const TMDB_KEY   = "proxy";
const TMDB_BASE  = "/api/tmdb-proxy";
const TMDB_IMG   = "https://image.tmdb.org/t/p/w300";

let subscriptionConfigPromise = null;
let subscriptionLoginPromise = null;
const SUBSCRIPTION_SESSION_KEY = "streamvault_subscription_token";
let subAuthMode = "login";
let currentSubscriber = null;
let subscriptionPollTimer = null;
let subscriptionCaptchaSiteKey = "";
let subscriptionCaptchaReadyPromise = null;

async function getSubscriptionConfig() {
  if (!subscriptionConfigPromise) {
    subscriptionConfigPromise = fetch(`${STREAM_API}/api/subscription/config`)
      .then(r => r.json())
      .catch(() => ({ enabled: false }));
  }
  return subscriptionConfigPromise;
}

function getCaptchaConfig(cfg) {
  return (cfg && cfg.captcha) ? cfg.captcha : { enabled: false, site_key: "" };
}

async function ensureSubscriptionCaptcha() {
  const cfg = getCaptchaConfig(await getSubscriptionConfig());
  if (!cfg.enabled || !cfg.site_key) {
    subscriptionCaptchaSiteKey = "";
    return false;
  }

  if (window.grecaptcha?.execute && subscriptionCaptchaSiteKey === cfg.site_key) return true;
  subscriptionCaptchaSiteKey = cfg.site_key;
  if (!document.querySelector(`script[data-recaptcha-site-key="${cfg.site_key}"]`)) {
    const script = document.createElement("script");
    script.src = `https://www.google.com/recaptcha/api.js?render=${encodeURIComponent(cfg.site_key)}&hl=id`;
    script.async = true;
    script.defer = true;
    script.dataset.recaptchaSiteKey = cfg.site_key;
    document.head.appendChild(script);
  }
  const ready = () => !!window.grecaptcha?.execute;
  if (ready()) return true;
  if (!subscriptionCaptchaReadyPromise) {
    subscriptionCaptchaReadyPromise = new Promise(resolve => {
      const start = Date.now();
      const timer = setInterval(() => {
        const isReady = ready();
        if (isReady || Date.now() - start > 10000) {
          clearInterval(timer);
          subscriptionCaptchaReadyPromise = null;
          resolve(isReady);
        }
      }, 150);
    });
  }
  return subscriptionCaptchaReadyPromise;
}

async function executeSubscriptionCaptcha(action) {
  const ready = await ensureSubscriptionCaptcha();
  if (!subscriptionCaptchaSiteKey) return "";
  if (!ready || !window.grecaptcha?.execute) return "";
  return new Promise(resolve => {
    grecaptcha.ready(() => {
      grecaptcha.execute(subscriptionCaptchaSiteKey, { action })
        .then(token => resolve(token || ""))
        .catch(() => resolve(""));
    });
  });
}

async function requireSubscriptionToken() {
  const cfg = await getSubscriptionConfig();
  if (!cfg.enabled) return "";

  const savedToken = localStorage.getItem(SUBSCRIPTION_SESSION_KEY) || "";
  if (savedToken) return savedToken;

  return showSubscriptionLogin();
}

function subscriptionHeaders() {
  const token = localStorage.getItem(SUBSCRIPTION_SESSION_KEY) || "";
  return token ? { "X-Subscription-Token": token } : {};
}

function setSubscriptionError(message) {
  const err = document.getElementById("subAuthError");
  if (!err) return;
  err.textContent = message || "";
  err.style.display = message ? "block" : "none";
}

function updateSubAuthMode() {
  const title = document.getElementById("subAuthTitle");
  const text = document.getElementById("subAuthText");
  const pinInput = document.getElementById("subAuthPin");
  const submit = document.getElementById("subAuthSubmit");
  const toggle = document.getElementById("subAuthToggle");
  if (!title || !text || !pinInput || !submit || !toggle) return;

  const isRegister = subAuthMode === "register";
  title.textContent = isRegister ? "REGISTER AKUN" : "MASUK AKUN";
  text.textContent = isRegister
    ? "Buat username dan password. Setelah register, kamu bisa pilih paket langganan."
    : "Masukkan username dan password. Kalau belum punya akun, daftar dulu di sini.";
  submit.textContent = isRegister ? "REGISTER" : "MASUK";
  toggle.textContent = isRegister ? "SUDAH PUNYA AKUN? MASUK" : "REGISTER AKUN BARU";
  pinInput.placeholder = "Password";
}

function showSubscriptionLogin(message = "") {
  if (subscriptionLoginPromise) return subscriptionLoginPromise;

  const gate = document.getElementById("subAuthGate");
  const form = document.getElementById("subAuthForm");
  const usernameInput = document.getElementById("subAuthUsername");
  const pinInput = document.getElementById("subAuthPin");
  const submit = document.getElementById("subAuthSubmit");
  const toggle = document.getElementById("subAuthToggle");

  if (!gate || !form || !usernameInput || !pinInput || !submit || !toggle) {
    return Promise.reject(new Error("UI login akun tidak tersedia."));
  }

  subAuthMode = "login";
  updateSubAuthMode();
  setSubscriptionError(message);
  pinInput.value = "";
  submit.disabled = true;
  gate.classList.add("visible");
  setTimeout(() => usernameInput.focus(), 30);
  ensureSubscriptionCaptcha().finally(() => {
    submit.disabled = false;
  });

  subscriptionLoginPromise = new Promise((resolve, reject) => {
    toggle.onclick = () => {
      subAuthMode = subAuthMode === "login" ? "register" : "login";
      updateSubAuthMode();
      setSubscriptionError("");
    };

    form.onsubmit = async e => {
      e.preventDefault();
      const username = usernameInput.value.trim().toLowerCase();
      const password = pinInput.value.trim();
      if (!username || !password) {
        setSubscriptionError("Username dan password wajib diisi.");
        return;
      }
      const captchaAction = subAuthMode === "register" ? "subscription_register" : "subscription_login";
      const captchaToken = await executeSubscriptionCaptcha(captchaAction);
      if (getCaptchaConfig(await getSubscriptionConfig()).enabled && !captchaToken) {
        setSubscriptionError("Captcha gagal dimuat. Coba refresh halaman.");
        return;
      }

      submit.disabled = true;
      submit.textContent = subAuthMode === "register" ? "MEMBUAT..." : "MEMERIKSA...";
      setSubscriptionError("");

      try {
        const data = subAuthMode === "register"
          ? await registerSubscription(username, password, captchaToken)
          : await loginSubscription(username, password, captchaToken);
        localStorage.setItem(SUBSCRIPTION_SESSION_KEY, data.token);
        currentSubscriber = data;
        updateSubscriptionUi(data.subscription);
        gate.classList.remove("visible");
        if (subAuthMode === "register" || !data.subscription?.active) {
          setTimeout(() => showSubscriptionPlans("Pilih paket langganan untuk membuka akses streaming."), 80);
        }
        resolve(data.token);
      } catch (err) {
        setSubscriptionError(err.message || "Login gagal.");
      } finally {
        submit.disabled = false;
        submit.textContent = subAuthMode === "register" ? "REGISTER" : "MASUK";
      }
    };
  }).finally(() => {
    subscriptionLoginPromise = null;
  });

  return subscriptionLoginPromise;
}

async function loginSubscription(username, password, captchaToken = "") {
  const res = await fetch(`${STREAM_API}/api/subscription/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, captcha_token: captchaToken }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.status !== "success" || !data.token) {
    throw new Error(data.message || "Login gagal.");
  }
  return data;
}

async function registerSubscription(username, password, captchaToken = "") {
  const res = await fetch(`${STREAM_API}/api/subscription/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, captcha_token: captchaToken }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.status !== "success" || !data.token) {
    throw new Error(data.message || "Register gagal.");
  }
  return data;
}

async function refreshSubscriptionMe() {
  const token = localStorage.getItem(SUBSCRIPTION_SESSION_KEY) || "";
  if (!token) return null;
  const res = await fetch(`${STREAM_API}/api/subscription/me`, { headers: subscriptionHeaders() });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.status !== "success") return null;
  currentSubscriber = data;
  updateSubscriptionUi(data.subscription);
  return data;
}

function updateSubscriptionUi(subscription) {
  const btn = document.getElementById("btnSubscription");
  if (!btn) return;
  if (subscription?.active) {
    btn.textContent = "AKTIF";
    btn.title = subscription.valid_until ? `Aktif sampai ${subscription.valid_until}` : "Langganan aktif";
  } else {
    btn.textContent = "LANGGANAN";
    btn.title = "Pilih paket langganan";
  }
}

function subscriptionActiveText(subscription) {
  if (!subscription?.active) return "";
  if (!subscription.valid_until) return "Langganan kamu sudah aktif.";
  try {
    const date = new Date(subscription.valid_until);
    return `Langganan aktif sampai ${date.toLocaleString("id-ID")}.`;
  } catch {
    return `Langganan aktif sampai ${subscription.valid_until}.`;
  }
}

function renderSubscriptionActive(subscription, autoClose = false) {
  const status = document.getElementById("subscriptionStatusText");
  const paymentEl = document.getElementById("subscriptionPayment");
  const state = document.getElementById("subscriptionPayState");
  updateSubscriptionUi(subscription);
  if (status) status.textContent = subscriptionActiveText(subscription);
  if (state) state.textContent = "Pembayaran berhasil. Langganan sudah aktif.";
  if (paymentEl) paymentEl.classList.remove("visible");
  if (subscriptionPollTimer) {
    clearInterval(subscriptionPollTimer);
    subscriptionPollTimer = null;
  }
  if (autoClose) setTimeout(closeSubscriptionModal, 1200);
}

async function showSubscriptionPlans(message = "") {
  const cfg = await getSubscriptionConfig();
  if (cfg.enabled && !localStorage.getItem(SUBSCRIPTION_SESSION_KEY)) {
    await showSubscriptionLogin("Login dulu sebelum memilih langganan.");
  }

  const modal = document.getElementById("subscriptionModal");
  const status = document.getElementById("subscriptionStatusText");
  const plansEl = document.getElementById("subscriptionPlans");
  const paymentEl = document.getElementById("subscriptionPayment");
  if (!modal || !status || !plansEl || !paymentEl) return;

  if (subscriptionPollTimer) {
    clearInterval(subscriptionPollTimer);
    subscriptionPollTimer = null;
  }
  paymentEl.classList.remove("visible");
  status.textContent = message || "Pilih paket untuk membuka akses streaming.";
  modal.classList.add("visible");

  const me = await refreshSubscriptionMe();
  if (me?.subscription?.active) {
    renderSubscriptionActive(me.subscription, false);
  }

  plansEl.innerHTML = [
    { id: "weekly", label: "Mingguan", price: "Rp 7.000", days: "7 hari akses" },
    { id: "monthly", label: "Bulanan", price: "Rp 20.000", days: "30 hari akses" },
  ].map(plan => `
    <button class="subscription-plan" type="button" onclick="createSubscriptionPayment('${plan.id}')">
      <div class="subscription-plan-name">${plan.label}</div>
      <div class="subscription-plan-price">${plan.price}</div>
      <div class="subscription-plan-days">${plan.days}</div>
    </button>
  `).join("");
}

function closeSubscriptionModal() {
  const modal = document.getElementById("subscriptionModal");
  if (modal) modal.classList.remove("visible");
  if (subscriptionPollTimer) {
    clearInterval(subscriptionPollTimer);
    subscriptionPollTimer = null;
  }
}

function getPaymentData(data) {
  return data?.payment || data?.raw?.data || data?.data || {};
}

async function createSubscriptionPayment(plan) {
  const status = document.getElementById("subscriptionStatusText");
  const paymentEl = document.getElementById("subscriptionPayment");
  const qr = document.getElementById("subscriptionQr");
  const link = document.getElementById("subscriptionPayLink");
  const state = document.getElementById("subscriptionPayState");
  if (!status || !paymentEl || !qr || !link || !state) return;

  status.textContent = "Membuat invoice pembayaran...";
  paymentEl.classList.add("visible");
  qr.textContent = "Memuat QRIS...";
  state.textContent = "Menyiapkan pembayaran...";
  link.style.display = "none";

  try {
    const me = await refreshSubscriptionMe();
    if (me?.subscription?.active) {
      renderSubscriptionActive(me.subscription, false);
      return;
    }

    const res = await fetch(`${STREAM_API}/api/subscription/payment/create`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...subscriptionHeaders() },
      body: JSON.stringify({ plan, redirect_url: location.href }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.status !== "success") throw new Error(data.message || "Gagal membuat pembayaran.");

    const payment = getPaymentData(data);
    const invoice = payment.invoice_id || payment.invoice || payment.id;
    const payUrl = payment.payment_url || payment.checkout_url || payment.url;
    const qrUrl = payment.qris_dynamic_image_url || payment.qris_static_image_url || payment.qr_url;
    if (qrUrl) qr.innerHTML = `<img src="${esc(qrUrl)}" alt="QRIS">`;
    else qr.textContent = "Buka halaman bayar";
    if (payUrl) {
      link.href = payUrl;
      link.style.display = "inline-flex";
    }
    status.textContent = `${data.plan?.label || "Paket"} dibuat. Selesaikan pembayaran lalu tunggu verifikasi otomatis.`;
    state.textContent = `Invoice ${invoice || ""} menunggu pembayaran...`;
    if (invoice && data.payment_token) startSubscriptionPolling(invoice, data.payment_token);
  } catch (err) {
    status.textContent = err.message || "Gagal membuat pembayaran.";
    state.textContent = "Gagal";
    qr.textContent = "Tidak bisa membuat QRIS";
  }
}

function startSubscriptionPolling(invoice, paymentToken) {
  const state = document.getElementById("subscriptionPayState");
  if (subscriptionPollTimer) clearInterval(subscriptionPollTimer);

  const verify = async () => {
    const me = await refreshSubscriptionMe();
    if (me?.subscription?.active) {
      renderSubscriptionActive(me.subscription, true);
      return;
    }

    const res = await fetch(`${STREAM_API}/api/subscription/payment/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...subscriptionHeaders() },
      body: JSON.stringify({ invoice, payment_token: paymentToken }),
    });
    const data = await res.json().catch(() => ({}));
    if (data.status === "success") {
      clearInterval(subscriptionPollTimer);
      subscriptionPollTimer = null;
      currentSubscriber = data;
      updateSubscriptionUi(data.subscription);
      renderSubscriptionActive(data.subscription, true);
      return;
    }
    if (state) state.textContent = data.payment_status ? `Status: ${data.payment_status}` : "Menunggu pembayaran...";
  };

  verify();
  subscriptionPollTimer = setInterval(verify, 5000);
}

async function appFetch(url, opt = {}) {
  const reqOpt = { ...opt, headers: { ...(opt.headers || {}) } };
  const token = await requireSubscriptionToken();
  const cfg = await getSubscriptionConfig();
  if (cfg.enabled && !token) throw new Error("Login langganan diperlukan.");
  if (token) reqOpt.headers["X-Subscription-Token"] = token;

  const res = await fetch(url, reqOpt);
  if (res.status !== 401 && res.status !== 403) return res;

  const body = await res.clone().json().catch(() => ({}));
  if (!body.subscription_required) return res;
  if (res.status === 401) {
    localStorage.removeItem(SUBSCRIPTION_SESSION_KEY);
    const freshToken = await showSubscriptionLogin(body.message || "Login diperlukan.");
    reqOpt.headers["X-Subscription-Token"] = freshToken;
  } else {
    await showSubscriptionPlans(body.message || "Langganan aktif diperlukan.");
    throw new Error(body.message || "Langganan aktif diperlukan.");
  }
  return fetch(url, reqOpt);
}

async function initSubscriptionGate() {
  const cfg = await getSubscriptionConfig();
  if (cfg.enabled && !localStorage.getItem(SUBSCRIPTION_SESSION_KEY)) {
    showSubscriptionLogin();
  } else if (cfg.enabled) {
    refreshSubscriptionMe();
  }
}

/* Items per page & max pages */
const PAGE_SIZE = 20;      // TMDb default per page
const MAX_GENRE_PAGES = 5; // 5 pages × 20 = 100 film per genre

/* ═══════════════════════════════════════
   STATE
═══════════════════════════════════════ */
let allResults     = [];
let searchPage     = 1;
let searchQuery    = "";
let currentType    = "all";
let currentGenre   = "all";  /* TMDb genre ID string */
let genrePageNum   = 1;
let genreLoading   = false;
let genreItems     = [];
let selectedItem   = null;
let hlsInst        = null;
let plyrInst       = null;
let barTimer       = null;

/* ── Rotate Lock State ── */
let rotateLocked   = false;   // apakah sedang dikunci
let lockOrientation = null;   // orientasi yang dikunci: 'landscape' | 'portrait'
let orientationMode = "browse"; // browse = portrait lock, player = fullscreen rotate

/* Subtitle */
const SUB = {
  imdbId: null,
  tmdbId: null,
  title: "",
  mediaType: "movie",
  season: null,
  episode: null,
  activeLang: localStorage.getItem("preferred_lang") || "id",
  activeFileId: null,
};
const LANGS = [
  {code:"en",label:"ENG"},{code:"id",label:"IND"},{code:"ja",label:"JPN"},
  {code:"ko",label:"KOR"},{code:"zh-CN",label:"ZHO"},{code:"ar",label:"ARA"},
  {code:"fr",label:"FRA"},{code:"es",label:"ESP"},
];
let _subCues = [], _subTimer = null;
let seasonModalItem = null;
let currentPlayback = {mediaType:"movie", tmdbId:null, season:1, episode:1, totalEpisodes:null, title:""};

async function subtitleFetch(url, opt = {}) {
  return appFetch(url, opt);
}

function saveFilmContinueProgress(videoEl) {
  try {
    if (!selectedItem || !videoEl || !isFinite(videoEl.duration) || videoEl.duration < 20) return;
    const pct = Math.max(0, Math.min(0.98, videoEl.currentTime / videoEl.duration));
    if (pct < .03) return;
    const id = `film_${currentPlayback.mediaType}_${selectedItem["#IMDB_ID"] || selectedItem._tmdb_id || currentPlayback.title}`;
    const all = JSON.parse(localStorage.getItem('cw_progress') || '[]');
    const existing = all.findIndex(x => x.id === id);
    const epTitle = currentPlayback.mediaType === 'tv'
      ? `S${currentPlayback.season}E${currentPlayback.episode}`
      : 'Film';
    const entry = {
      id,
      mediaType: currentPlayback.mediaType === 'tv' ? 'tv' : 'movie',
      title: currentPlayback.title || selectedItem["#TITLE"] || 'Film',
      poster: selectedItem["#IMG_POSTER"] || '',
      href: '#',
      epTitle,
      pct,
      item: selectedItem,
    };
    if (existing >= 0) all[existing] = entry; else all.unshift(entry);
    if (all.length > 24) all.length = 24;
    localStorage.setItem('cw_progress', JSON.stringify(all));
    if (typeof renderContinueWatching === 'function') renderContinueWatching();
  } catch {}
}

/* ═══════════════════════════════════════
   NAV TABS
═══════════════════════════════════════ */
function setNTab(type) {
  currentType = type;
  document.querySelectorAll(".ntab").forEach(t => t.classList.remove("active"));
  const map = {all:"ntAll",movie:"ntMovie",tvseries:"ntSeries"};
  document.getElementById(map[type]||"ntAll").classList.add("active");

  /* Jika sedang browse genre, refresh grid */
  if (currentGenre !== "all") {
    genreItems = [];
    genrePageNum = 1;
    document.getElementById("genreGrid").innerHTML = "";
    loadGenrePage();
  } else {
    applyFilter();
  }
}

/* ═══════════════════════════════════════
   GENRE FILTER — FIXED
   Setiap chip memanggil TMDb /discover dengan genre ID
   dan menampilkan sampai 99+ film lewat pagination
═══════════════════════════════════════ */
function setGenre(el) {
  const genre = el.dataset.genre;

  /* Update chip aktif */
  document.querySelectorAll(".chip").forEach(c => c.classList.remove("active"));
  el.classList.add("active");

  /* Scroll chip ke posisi terlihat */
  el.scrollIntoView({behavior:"smooth", block:"nearest", inline:"center"});

  currentGenre = genre;

  if (genre === "all") {
    /* Tampilkan reco area */
    document.getElementById("recoArea").style.display = "";
    document.getElementById("searchResultArea").style.display = "none";
    document.getElementById("genreBrowseArea").style.display = "none";
    return;
  }

  /* Sembunyikan reco & search, tampilkan genre browse */
  document.getElementById("recoArea").style.display = "none";
  document.getElementById("searchResultArea").style.display = "none";
  document.getElementById("genreBrowseArea").style.display = "block";

  /* Reset & load */
  genreItems   = [];
  genrePageNum = 1;
  document.getElementById("genreGrid").innerHTML = "";
  document.getElementById("btnLoadMoreGenre").style.display = "none";

  loadGenrePage();
}

async function loadGenrePage() {
  if (genreLoading) return;
  genreLoading = true;

  setStatus(true, `Memuat genre... halaman ${genrePageNum}`);
  const grid = document.getElementById("genreGrid");

  try {
    /* Tentukan endpoint berdasarkan tipe */
    const mediaType = currentType === "tvseries" ? "tv" : "movie";
    const genreParam = currentGenre !== "all" ? `&with_genres=${currentGenre}` : "";
    const url = `${TMDB_BASE}/discover/${mediaType}?api_key=${TMDB_KEY}&language=id-ID&sort_by=popularity.desc&page=${genrePageNum}${genreParam}`;

    const r = await fetch(url);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const raw = await r.json();
    const results = (raw.results || []).filter(i => i.poster_path);

    results.forEach((item, i) => {
      const m = tmdbToLocal(item);
      genreItems.push(m);

      const card = makeMovieCard(m, genreItems.length - 1, i);
      grid.appendChild(card);
    });

    const totalPages = raw.total_pages || 1;
    const hasMore = genrePageNum < totalPages && genrePageNum < MAX_GENRE_PAGES;
    const btnMore = document.getElementById("btnLoadMoreGenre");
    btnMore.style.display = hasMore ? "block" : "none";
    if (hasMore) {
      btnMore.textContent = `↓ MUAT LEBIH BANYAK (${genreItems.length} / ${Math.min(raw.total_results, MAX_GENRE_PAGES * PAGE_SIZE)}+)`;
    }

  } catch(e) {
    grid.innerHTML += `<div class="empty" style="grid-column:1/-1">
      <h3>GAGAL</h3><p>${esc(e.message)}</p>
    </div>`;
  }

  setStatus(false);
  genreLoading = false;
}

async function loadMoreGenre() {
  genrePageNum++;
  const btn = document.getElementById("btnLoadMoreGenre");
  btn.disabled = true;
  btn.textContent = "Memuat...";
  await loadGenrePage();
  btn.disabled = false;
}

/* ═══════════════════════════════════════
   SEARCH
═══════════════════════════════════════ */
function setStatus(show, txt) {
  const el = document.getElementById("statusBar");
  el.style.display = show ? "flex" : "none";
  if (txt) document.getElementById("statusTxt").textContent = txt;
}

async function doSearch() {
  const q = document.getElementById("searchInput").value.trim();
  if (!q) {
    /* Reset ke reco */
    document.getElementById("recoArea").style.display = "";
    document.getElementById("searchResultArea").style.display = "none";
    document.getElementById("genreBrowseArea").style.display = "none";
    /* Reset genre chips */
    document.querySelectorAll(".chip").forEach(c => c.classList.remove("active"));
    document.querySelector(".chip[data-genre='all']").classList.add("active");
    currentGenre = "all";
    return;
  }

  searchQuery = q;
  searchPage  = 1;

  document.getElementById("recoArea").style.display = "none";
  document.getElementById("genreBrowseArea").style.display = "none";
  document.getElementById("searchResultArea").style.display = "block";
  document.getElementById("btnSearch").disabled = true;
  setStatus(true, `Mencari "${q}"...`);
  document.getElementById("movieGrid").innerHTML = "";
  document.getElementById("btnLoadMore").style.display = "none";
  allResults = [];

  try {
    const r = await fetch(`${IMDB_API}/search?q=${encodeURIComponent(q)}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const raw = await r.json();

    if (Array.isArray(raw)) allResults = raw;
    else if (raw.description && Array.isArray(raw.description)) allResults = raw.description;
    else if (typeof raw === "object") {
      allResults = Object.values(raw).filter(v => v && typeof v === "object" && v["#IMDB_ID"]);
    }

    applyFilter();
  } catch(e) {
    document.getElementById("movieGrid").innerHTML = `
      <div class="empty"><h3>GAGAL</h3><p>${esc(e.message)}</p></div>`;
  }

  setStatus(false);
  document.getElementById("btnSearch").disabled = false;
}

document.getElementById("searchInput")
  .addEventListener("keydown", e => e.key === "Enter" && doSearch());

async function loadMoreSearch() {
  searchPage++;
  const btn = document.getElementById("btnLoadMore");
  btn.disabled = true;
  btn.textContent = "Memuat...";

  try {
    const r = await fetch(`${IMDB_API}/search?q=${encodeURIComponent(searchQuery)}&page=${searchPage}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const raw = await r.json();

    let newItems = [];
    if (Array.isArray(raw)) newItems = raw;
    else if (raw.description && Array.isArray(raw.description)) newItems = raw.description;
    else if (typeof raw === "object") {
      newItems = Object.values(raw).filter(v => v && typeof v === "object" && v["#IMDB_ID"]);
    }

    allResults = [...allResults, ...newItems];
    if (newItems.length > 0) {
      const grid = document.getElementById("movieGrid");
      const offset = allResults.length - newItems.length;
      newItems.forEach((m, i) => {
        grid.appendChild(makeMovieCard(m, offset + i, i));
      });
    }
    btn.style.display = newItems.length >= 10 ? "block" : "none";
  } catch {}

  btn.disabled = false;
  btn.textContent = "↓ MUAT LEBIH BANYAK";
}

/* ═══════════════════════════════════════
   FILTER & RENDER (untuk search results)
═══════════════════════════════════════ */
function applyFilter() {
  let data = [...allResults];
  if (currentType !== "all") {
    data = data.filter(m => (m["#TYPE"]||"").toLowerCase() === currentType);
  }
  renderGrid(data);
}

function renderGrid(items) {
  const grid = document.getElementById("movieGrid");
  grid.innerHTML = "";
  if (!items.length) {
    grid.innerHTML = `<div class="empty"><h3>TIDAK ADA HASIL</h3><p>Coba kata kunci lain.</p></div>`;
    document.getElementById("btnLoadMore").style.display = "none";
    return;
  }
  items.forEach((m, i) => grid.appendChild(makeMovieCard(m, i, i)));
  document.getElementById("btnLoadMore").style.display = items.length >= 10 ? "block" : "none";
  if (items.length >= 10) {
    document.getElementById("btnLoadMore").textContent = `↓ MUAT LEBIH BANYAK (${items.length} hasil)`;
  }
}

/* ─── Factory: buat elemen card ─── */
function makeMovieCard(m, dataIdx, animIdx) {
  const title  = m["#TITLE"]       || "Untitled";
  const year   = m["#YEAR"]        || "";
  const rating = m["#IMDB_RATING"] || "";
  const type   = (m["#TYPE"]||"movie").toLowerCase();
  const img    = m["#IMG_POSTER"]  || "";
  const typeLabel = type==="tvseries"?"SERIAL":type==="tvepisode"?"EPISODE":"FILM";
  const typeClass = type==="tvseries"?"type-series":type==="tvepisode"?"type-episode":"type-movie";

  const card = document.createElement("div");
  card.className = "movie-card";
  card.style.animationDelay = Math.min(animIdx * 30, 500) + "ms";
  card.onclick = () => openDetail(m);
  card.innerHTML = `
    <div class="card-poster">
      ${img
        ? `<img src="${esc(img)}" alt="${esc(title)}" loading="lazy"
             onerror="this.parentNode.innerHTML='<div class=no-img-icon>NO IMAGE</div>'">`
        : `<div class="no-img-icon">NO IMAGE</div>`}
      ${rating ? `<div class="card-rating">RATING ${esc(rating)}</div>` : ""}
      <div class="card-type-badge ${typeClass}">${typeLabel}</div>
    </div>
    <div class="card-info">
      <div class="card-title" title="${esc(title)}">${esc(title)}</div>
      <div class="card-year">${esc(String(year))}</div>
    </div>`;
  return card;
}

/* ═══════════════════════════════════════
   RECOMMENDATION (TMDb)
═══════════════════════════════════════ */
const RECO_CATEGORIES = [
  {id:"recoTrending",icon:"",title:"TRENDING HARI INI",badge:"POPULER",endpoint:"/trending/all/day"},
  {id:"recoNowPlay", icon:"",title:"SEDANG TAYANG",   badge:"BIOSKOP",endpoint:"/movie/now_playing"},
  {id:"recoAction",  icon:"",title:"ACTION",          badge:"TOP",    endpoint:"/discover/movie?with_genres=28&sort_by=popularity.desc"},
  {id:"recoComedy",  icon:"",title:"KOMEDI",          badge:"FUN",    endpoint:"/discover/movie?with_genres=35&sort_by=popularity.desc"},
  {id:"recoDrama",   icon:"",title:"DRAMA",           badge:"PILIHAN",endpoint:"/discover/movie?with_genres=18&sort_by=vote_average.desc&vote_count.gte=500"},
  {id:"recoAnime",   icon:"",title:"ANIME",           badge:"OTAKU",  endpoint:"/discover/movie?with_genres=16&sort_by=popularity.desc"},
  {id:"recoHorror",  icon:"",title:"HORROR",          badge:"SERAM",  endpoint:"/discover/movie?with_genres=27&sort_by=popularity.desc"},
  {id:"recoTV",      icon:"",title:"SERIAL TV POPULER",badge:"BINGE", endpoint:"/tv/popular"},
];

function tmdbToLocal(item) {
  const isTV = item.media_type==="tv"||(item.name&&!item.title);
  return {
    "#TITLE":       item.title||item.name||"Untitled",
    "#YEAR":        (item.release_date||item.first_air_date||"").substring(0,4),
    "#IMDB_ID":     null,
    "#IMG_POSTER":  item.poster_path ? TMDB_IMG+item.poster_path : "",
    "#IMDB_RATING": item.vote_average ? item.vote_average.toFixed(1) : "",
    "#TYPE":        isTV?"tvseries":"movie",
    "#DESCRIPTION": item.overview||"",
    "#AKA":         item.original_title||item.original_name||"",
    "#GENRE":       "",
    "_tmdb_id":     item.id,
    "_tmdb_type":   isTV?"tv":"movie",
  };
}

function renderRecoSkeleton(cid, title, icon, badge) {
  const el = document.getElementById(cid);
  const skels = Array(8).fill(0).map(() => `
    <div class="reco-skeleton">
      <div class="skel-poster"></div>
      <div class="skel-info">
        <div class="skel-line"></div>
        <div class="skel-line short"></div>
      </div>
    </div>`).join("");
  el.innerHTML = `
    <div class="reco-header">
      <div class="reco-title">${title}</div>
      <div class="reco-badge">${badge}</div>
    </div>
    <div class="reco-row">${skels}</div>`;
}

function renderRecoCards(cid, title, icon, badge, items) {
  const el = document.getElementById(cid);
  const cards = items.slice(0,20).map((m, i) => {
    const t   = m["#TITLE"]||"Untitled";
    const yr  = m["#YEAR"]||"";
    const img = m["#IMG_POSTER"]||"";
    const rat = m["#IMDB_RATING"]||"";
    const type = (m["#TYPE"]||"movie").toLowerCase();
    const typeLabel = type==="tvseries"?"SERIAL":"FILM";
    const typeClass = type==="tvseries"?"type-series":"type-movie";
    const dataAttr = esc(JSON.stringify(m));
    return `
      <div class="reco-card" style="animation-delay:${Math.min(i*40,600)}ms"
           onclick='openDetail(JSON.parse(this.dataset.m))' data-m="${dataAttr}">
        <div class="card-poster">
          ${img
            ? `<img src="${esc(img)}" alt="${esc(t)}" loading="lazy"
                 onerror="this.parentNode.innerHTML='<div class=no-img-icon>NO IMAGE</div>'">`
            : `<div class="no-img-icon">NO IMAGE</div>`}
          ${rat ? `<div class="card-rating">RATING ${esc(rat)}</div>` : ""}
          <div class="card-type-badge ${typeClass}">${typeLabel}</div>
        </div>
        <div class="card-info">
          <div class="card-title" title="${esc(t)}">${esc(t)}</div>
          <div class="card-year">${esc(String(yr))}</div>
        </div>
      </div>`;
  }).join("");
  el.innerHTML = `
    <div class="reco-header">
      <div class="reco-title">${title}</div>
      <div class="reco-badge">${badge}</div>
    </div>
    <div class="reco-row">${cards}</div>`;
}

async function fetchRecoCategory(cat) {
  try {
    const sep = cat.endpoint.includes("?") ? "&" : "?";
    const url = `${TMDB_BASE}${cat.endpoint}${sep}api_key=${TMDB_KEY}&language=id-ID&page=1`;
    const r = await fetch(url);
    if (!r.ok) return;
    const raw = await r.json();
    const results = (raw.results||[]).filter(i => i.poster_path);
    if (results.length) {
      renderRecoCards(cat.id, cat.title, cat.icon, cat.badge, results.map(tmdbToLocal));
    }
  } catch {}
}

async function loadRecommendations() {
  if (!TMDB_KEY) return;
  RECO_CATEGORIES.forEach(cat => renderRecoSkeleton(cat.id, cat.title, cat.icon, cat.badge));
  await Promise.allSettled(RECO_CATEGORIES.map(cat => fetchRecoCategory(cat)));
}
loadRecommendations();

/* ═══════════════════════════════════════
   DETAIL PANEL
═══════════════════════════════════════ */
function isTVItem(m) {
  return (m?.["#TYPE"] || "").toLowerCase() === "tvseries" || m?._tmdb_type === "tv";
}

async function ensureTmdbMeta(m) {
  if (!m) return null;
  if (m._tmdb_id) return m;
  const imdbId = m["#IMDB_ID"];
  if (!imdbId || !TMDB_KEY) return m;

  try {
    const r = await fetch(`${TMDB_BASE}/find/${encodeURIComponent(imdbId)}?api_key=${TMDB_KEY}&language=id-ID&external_source=imdb_id`);
    if (!r.ok) return m;
    const data = await r.json();
    const wantTV = isTVItem(m);
    const found = wantTV ? (data.tv_results || [])[0] : (data.movie_results || [])[0];
    if (!found) return m;
    m._tmdb_id = found.id;
    m._tmdb_type = wantTV ? "tv" : "movie";
    if (found.poster_path && !m["#IMG_POSTER"]) m["#IMG_POSTER"] = TMDB_IMG + found.poster_path;
    if (found.overview && !m["#DESCRIPTION"]) m["#DESCRIPTION"] = found.overview;
  } catch {}
  return m;
}

async function openDetail(m) {
  selectedItem = m;

  /* Resolve IMDB ID dari TMDb jika belum ada */
  if (!m["#IMDB_ID"] && m._tmdb_id && TMDB_KEY) {
    try {
      const type = m._tmdb_type||"movie";
      const [extR, detR] = await Promise.all([
        fetch(`${TMDB_BASE}/${type}/${m._tmdb_id}/external_ids?api_key=${TMDB_KEY}`),
        fetch(`${TMDB_BASE}/${type}/${m._tmdb_id}?api_key=${TMDB_KEY}&language=id-ID`),
      ]);
      const ext = await extR.json();
      const det = await detR.json();
      if (ext.imdb_id) m["#IMDB_ID"] = ext.imdb_id;
      if (det.genres)  m["#GENRE"] = det.genres.map(g=>g.name).join(", ");
      if (det.overview) m["#DESCRIPTION"] = det.overview;
    } catch {}
    selectedItem = m;
  }

  const id     = m["#IMDB_ID"]     || "";
  const title  = m["#TITLE"]       || "Untitled";
  const year   = m["#YEAR"]        || "";
  const rating = m["#IMDB_RATING"] || "";
  const type   = (m["#TYPE"]||"movie").toLowerCase();
  const img    = m["#IMG_POSTER"]  || "";
  const desc   = m["#DESCRIPTION"] || m["#AKA"] || "Deskripsi tidak tersedia.";
  const genre  = m["#GENRE"]       || "";

  document.getElementById("dpTitle").textContent = title;
  document.getElementById("dpDesc").textContent  = desc;
  document.getElementById("dpPoster").src = img||"";
  document.getElementById("btnStream").textContent =
    type === "tvseries" ? "PILIH EPISODE" : "TONTON SEKARANG";

  const badges = document.getElementById("dpBadges");
  badges.innerHTML = "";
  if (year)   badges.innerHTML += `<span class="badge b-year">${esc(String(year))}</span>`;
  if (rating) badges.innerHTML += `<span class="badge b-rating">★ ${esc(rating)}</span>`;
  badges.innerHTML += `<span class="badge b-type">${
    type==="tvseries"?"Serial TV":type==="tvepisode"?"Episode":"Film"
  }</span>`;
  genre.split(",").slice(0,3).forEach(g => {
    if (g.trim()) badges.innerHTML += `<span class="badge b-genre">${esc(g.trim())}</span>`;
  });

  document.getElementById("providersWrap").style.display = "none";
  document.getElementById("photosWrap").style.display    = "none";
  document.getElementById("provList").innerHTML          = "";
  document.getElementById("photosList").innerHTML        = "";
  document.getElementById("detailPanel").style.display  = "block";

  if (id) { fetchProviders(id); fetchPhotos(id); }
}

function closeDetail() {
  document.getElementById("detailPanel").style.display = "none";
}

function openImdb() {
  const id = selectedItem?.["#IMDB_ID"];
  if (id) window.open(`https://www.imdb.com/title/${id}/`, "_blank");
}

async function fetchProviders(imdbId) {
  try {
    const r = await fetch(`${IMDB_API}/justwatch?q=${imdbId}`);
    if (!r.ok) return;
    const data = await r.json();
    const providers = new Set();
    const collect = obj => {
      if (!obj||typeof obj!=="object") return;
      ["stream","buy","rent","free","flatrate"].forEach(key => {
        if (Array.isArray(obj[key])) obj[key].forEach(p => { if(p.name) providers.add(p.name); });
      });
    };
    if (data.result) collect(data.result);
    else Object.values(data).forEach(v => { if(v&&typeof v==="object") collect(v); });
    if (providers.size > 0) {
      document.getElementById("provList").innerHTML =
        [...providers].slice(0,10).map(p => `<span class="prov-chip">${esc(p)}</span>`).join("");
      document.getElementById("providersWrap").style.display = "block";
    }
  } catch {}
}

async function fetchPhotos(imdbId) {
  try {
    const r = await fetch(`${IMDB_API}/photo/${imdbId}`);
    if (!r.ok) return;
    const data = await r.json();
    let urls = [];
    if (Array.isArray(data)) {
      urls = data.map(p => typeof p==="string"?p:p.url||p.src||"").filter(Boolean);
    } else if (typeof data==="object") {
      const flatten = obj => {
        Object.values(obj).forEach(v => {
          if (typeof v==="string"&&v.startsWith("http")) urls.push(v);
          else if (Array.isArray(v)) v.forEach(x => {
            if (typeof x==="string"&&x.startsWith("http")) urls.push(x);
            else if (x&&x.url) urls.push(x.url);
          });
          else if (v&&typeof v==="object") flatten(v);
        });
      };
      flatten(data);
    }
    urls = [...new Set(urls)].slice(0,10);
    if (urls.length > 0) {
      document.getElementById("photosList").innerHTML = urls.map(u =>
        `<div class="photo-thumb">
           <img src="${esc(u)}" loading="lazy" onerror="this.parentNode.style.display='none'">
         </div>`).join("");
      document.getElementById("photosWrap").style.display = "block";
    }
  } catch {}
}

/* ═══════════════════════════════════════
   STREAM
═══════════════════════════════════════ */
async function openSeasonModal(item) {
  seasonModalItem = await ensureTmdbMeta(item || selectedItem);
  selectedItem = seasonModalItem;
  closeDetail();

  const modal = document.getElementById("seasonModal");
  const seasonList = document.getElementById("seasonList");
  const episodePanel = document.getElementById("episodePanel");
  const title = seasonModalItem?.["#TITLE"] || "Serial";

  document.getElementById("seasonModalTitle").textContent = title;
  document.getElementById("seasonModalMeta").textContent = "Memuat season...";
  seasonList.innerHTML = "";
  episodePanel.innerHTML = `<div class="episode-empty">Memuat episode...</div>`;
  modal.style.display = "block";

  const tmdbId = seasonModalItem?._tmdb_id;
  if (!tmdbId) {
    document.getElementById("seasonModalMeta").textContent = "TMDB ID tidak tersedia.";
    episodePanel.innerHTML = `<div class="episode-empty">Serial ini belum punya TMDB ID, jadi episode belum bisa dipilih.</div>`;
    return;
  }

  try {
    const r = await fetch(`${TMDB_BASE}/tv/${tmdbId}?api_key=${TMDB_KEY}&language=id-ID`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    const seasons = (data.seasons || []).filter(s => s.season_number > 0 && s.episode_count > 0);
    document.getElementById("seasonModalMeta").textContent = `${seasons.length || 0} season`;

    if (!seasons.length) {
      episodePanel.innerHTML = `<div class="episode-empty">Daftar season tidak tersedia.</div>`;
      return;
    }

    seasonList.innerHTML = seasons.map(s => `
      <button class="season-item" data-season="${s.season_number}" data-count="${s.episode_count}"
        onclick="selectSeason(${s.season_number},${s.episode_count},this)">
        Season ${s.season_number}
      </button>
    `).join("");

    const first = seasonList.querySelector(".season-item");
    if (first) first.click();
  } catch(e) {
    document.getElementById("seasonModalMeta").textContent = "Gagal memuat season.";
    episodePanel.innerHTML = `<div class="episode-empty">Gagal mengambil season: ${esc(e.message)}</div>`;
  }
}

function closeSeasonModal() {
  document.getElementById("seasonModal").style.display = "none";
}

async function selectSeason(seasonNum, episodeCount, el) {
  document.querySelectorAll(".season-item").forEach(x => x.classList.remove("active"));
  if (el) el.classList.add("active");

  const episodePanel = document.getElementById("episodePanel");
  episodePanel.innerHTML = `<div class="episode-empty">Memuat Season ${seasonNum}...</div>`;
  const tmdbId = seasonModalItem?._tmdb_id;

  try {
    const r = await fetch(`${TMDB_BASE}/tv/${tmdbId}/season/${seasonNum}?api_key=${TMDB_KEY}&language=id-ID`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    renderEpisodeGrid(seasonNum, data.episodes || [], episodeCount);
  } catch {
    const fallback = Array.from({length: episodeCount || 1}, (_, i) => ({episode_number:i + 1}));
    renderEpisodeGrid(seasonNum, fallback, episodeCount);
  }
}

function renderEpisodeGrid(seasonNum, episodes, episodeCount) {
  const list = (episodes && episodes.length)
    ? episodes
    : Array.from({length: episodeCount || 1}, (_, i) => ({episode_number:i + 1}));

  document.getElementById("episodePanel").innerHTML = `
    <div class="episode-grid">
      ${list.map(ep => {
        const n = ep.episode_number || ep.episode || 1;
        return `<button class="episode-item" title="${esc(ep.name || `Episode ${n}`)}" onclick="playEpisode(${seasonNum},${n})">${n}</button>`;
      }).join("")}
    </div>
  `;
}

function playEpisode(season, episode) {
  closeSeasonModal();
  startTvEpisodeStream(season, episode);
}

/* ── Redesigned Player View Helpers ── */
let playerSources = {
  proxy: "",
  direct: "",
  embed: "",
  active: "proxy"
};

function getLastWatchedEpisode(item) {
  try {
    const mediaType = isTVItem(item) ? 'tv' : 'movie';
    const id = `film_${mediaType}_${item["#IMDB_ID"] || item._tmdb_id || item["#TITLE"]}`;
    const all = JSON.parse(localStorage.getItem('cw_progress') || '[]');
    const found = all.find(x => x.id === id);
    if (found && found.epTitle && found.epTitle.startsWith('S')) {
      const m = found.epTitle.match(/S(\d+)E(\d+)/);
      if (m) {
        return { season: parseInt(m[1]), episode: parseInt(m[2]) };
      }
    }
  } catch {}
  return { season: 1, episode: 1 };
}

function updatePlayerMetadata() {
  const item = selectedItem;
  if (!item) return;
  
  const title = item["#TITLE"] || item.title || "Unknown Title";
  const desc = item["#DESC"] || item.description || item.plot || "Sinopsis tidak tersedia.";
  const year = item["#YEAR"] || item.year || "";
  const rating = item["#RATING"] || item.rating || "";
  
  document.getElementById("playerPageTitle").textContent = title;
  document.getElementById("playerInfoTitle").textContent = title;
  document.getElementById("playerInfoDesc").textContent = desc;
  
  // Set backdrop
  const bgUrl = item.backdrop || item["#IMG_POSTER"] || item.poster || "";
  const bgEl = document.getElementById("playerBackdropBg");
  if (bgEl) {
    bgEl.style.backgroundImage = bgUrl ? `url(${bgUrl})` : "none";
  }
  
  let badges = "";
  if (year) badges += `<span class="badge">${year}</span>`;
  if (rating) badges += `<span class="badge" style="color:#d7c98f">★ ${rating}</span>`;
  
  const genres = item["#GENRES"] || item.genre || "";
  if (genres) {
    const list = Array.isArray(genres) ? genres : String(genres).split(",").map(x => x.trim());
    badges += list.map(g => `<span class="badge">${g}</span>`).join("");
  }
  
  document.getElementById("playerInfoMeta").innerHTML = badges;
}

function updateServerTabs(data, imdbId, season, episode) {
  playerSources = {
    proxy: data.stream_url || data.streamUrl || data.link || "",
    direct: data.rawStreamUrl || "",
    embed: "",
    active: "proxy"
  };
  
  // Construct embed URL for streamimdb.ru
  if (imdbId) {
    if (currentPlayback.mediaType === "tv") {
      playerSources.embed = `https://streamimdb.ru/embed/tv/${imdbId}/${season}/${episode}`;
    } else {
      playerSources.embed = `https://streamimdb.ru/embed/movie/${imdbId}`;
    }
  } else if (data.imdbId || data.imdb_id) {
    const id = data.imdbId || data.imdb_id;
    if (currentPlayback.mediaType === "tv") {
      playerSources.embed = `https://streamimdb.ru/embed/tv/${id}/${season}/${episode}`;
    } else {
      playerSources.embed = `https://streamimdb.ru/embed/movie/${id}`;
    }
  }

  const container = document.getElementById("playerServerTabs");
  container.innerHTML = "";
  
  const addTab = (id, label) => {
    const btn = document.createElement("button");
    btn.className = `server-tab-btn ${playerSources.active === id ? 'active' : ''}`;
    btn.textContent = label;
    btn.onclick = () => selectServerSource(id);
    container.appendChild(btn);
  };
  
  if (playerSources.proxy) {
    addTab("proxy", "Server 1 (HLS Proxy)");
  }
  if (playerSources.direct) {
    addTab("direct", "Server 2 (Direct HLS)");
  }
  if (playerSources.embed) {
    addTab("embed", "Server Embed (streamimdb.ru)");
  }
}

function selectServerSource(id) {
  playerSources.active = id;
  const btns = document.querySelectorAll(".server-tab-btn");
  
  if (id === "proxy" && btns[0]) {
    btns.forEach(b => b.classList.remove("active"));
    btns[0].classList.add("active");
  } else if (id === "direct") {
    btns.forEach(b => b.classList.remove("active"));
    const match = Array.from(btns).find(b => b.textContent.includes("Direct"));
    if (match) match.classList.add("active");
  } else if (id === "embed") {
    btns.forEach(b => b.classList.remove("active"));
    const match = Array.from(btns).find(b => b.textContent.includes("Embed"));
    if (match) match.classList.add("active");
  }
  
  const v = document.getElementById("mainVideo");
  const vc = document.getElementById("videoContainer");
  const f = document.getElementById("playerFrame");
  
  if (id === "proxy" || id === "direct") {
    f.src = "";
    f.style.display = "none";
    vc.style.display = "block";
    const url = id === "proxy" ? playerSources.proxy : playerSources.direct;
    const alternateStreamEndpoint = SUB.imdbId ? `${STREAM_API}/api/get-video?id=${encodeURIComponent(SUB.imdbId)}` : "";
    playHls(url, {alternateStreamEndpoint, forceHls: true});
  } else if (id === "embed") {
    if (hlsInst) { hlsInst.destroy(); hlsInst = null; }
    if (plyrInst) { plyrInst.destroy(); plyrInst = null; }
    v.pause();
    v.src = "";
    vc.style.display = "none";
    
    hidePOverlays();
    f.style.display = "block";
    f.src = playerSources.embed;
    
    // Hide controls overlay since embed has its own
    document.getElementById("playerBar").classList.add("faded");
  }
}

async function renderPlayerEpisodeSection() {
  const section = document.getElementById("playerEpisodeSection");
  if (currentPlayback.mediaType !== "tv" || !currentPlayback.tmdbId) {
    section.style.display = "none";
    return;
  }
  
  section.style.display = "block";
  const seasonRow = document.getElementById("playerSeasonRow");
  const episodeGrid = document.getElementById("playerEpisodeGrid");
  
  seasonRow.innerHTML = "Memuat...";
  episodeGrid.innerHTML = "";
  
  try {
    const r = await fetch(`${TMDB_BASE}/tv/${currentPlayback.tmdbId}?api_key=${TMDB_KEY}&language=id-ID`);
    if (!r.ok) throw new Error();
    const data = await r.json();
    const seasons = (data.seasons || []).filter(s => s.season_number > 0 && s.episode_count > 0);
    
    if (!seasons.length) {
      seasonRow.innerHTML = "Season tidak tersedia.";
      return;
    }
    
    seasonRow.innerHTML = seasons.map(s => `
      <button class="player-season-btn ${s.season_number === currentPlayback.season ? 'active' : ''}" 
        onclick="selectPlayerSeason(${s.season_number}, ${s.episode_count}, this)">
        Season ${s.season_number}
      </button>
    `).join("");
    
    await selectPlayerSeason(currentPlayback.season, seasons.find(s => s.season_number === currentPlayback.season)?.episode_count || 1);
  } catch {
    seasonRow.innerHTML = "Gagal memuat season.";
  }
}

async function selectPlayerSeason(seasonNum, episodeCount, el) {
  document.querySelectorAll(".player-season-btn").forEach(btn => btn.classList.remove("active"));
  if (el) {
    el.classList.add("active");
  } else {
    const btns = document.querySelectorAll(".player-season-btn");
    const match = Array.from(btns).find(b => b.textContent.includes(`Season ${seasonNum}`));
    if (match) match.classList.add("active");
  }
  
  const grid = document.getElementById("playerEpisodeGrid");
  grid.innerHTML = "Memuat episode...";
  
  let episodesList = [];
  try {
    const r = await fetch(`${TMDB_BASE}/tv/${currentPlayback.tmdbId}/season/${seasonNum}?api_key=${TMDB_KEY}&language=id-ID`);
    if (r.ok) {
      const data = await r.json();
      episodesList = data.episodes || [];
    }
  } catch {}
  
  if (episodesList.length === 0) {
    episodesList = Array.from({length: episodeCount}, (_, i) => ({episode_number: i+1}));
  }
  
  grid.innerHTML = episodesList.map(ep => {
    const n = ep.episode_number || 1;
    const isActive = seasonNum === currentPlayback.season && n === currentPlayback.episode;
    return `
      <button class="player-episode-btn ${isActive ? 'active' : ''}" 
        title="${esc(ep.name || `Episode ${n}`)}" 
        onclick="playPlayerEpisode(${seasonNum}, ${n})">
        ${n}
      </button>
    `;
  }).join("");
}

function playPlayerEpisode(season, episode) {
  startTvEpisodeStream(season, episode);
}

async function startStream() {
  if (!selectedItem) return;
  if (isTVItem(selectedItem)) {
    const last = getLastWatchedEpisode(selectedItem);
    startTvEpisodeStream(last.season, last.episode);
    return;
  }

  const id    = selectedItem["#IMDB_ID"] || "";
  const title = selectedItem["#TITLE"]   || "Film";
  if (!id) { alert("IMDB ID tidak tersedia untuk film ini."); return; }

  SUB.imdbId       = id;
  SUB.tmdbId       = selectedItem._tmdb_id || null;
  SUB.title        = title;
  SUB.mediaType    = "movie";
  SUB.season       = null;
  SUB.episode      = null;
  SUB.activeFileId = null;
  SUB.activeLang   = localStorage.getItem("preferred_lang") || "id";
  resetSubtitlePanel();
  currentPlayback = {mediaType:"movie", tmdbId:selectedItem._tmdb_id || null, season:1, episode:1, totalEpisodes:null, title};
  document.getElementById("episodeNav").style.display = "none";

  enterBrowseOrientationMode();
  closeDetail();
  
  updatePlayerMetadata();
  document.getElementById("playerMode").style.display = "block";
  showPLoading(`Mengambil stream untuk "${title}"...`);
  document.getElementById("playerTitle").textContent =
    `${title}${selectedItem["#YEAR"] ? " ("+selectedItem["#YEAR"]+")" : ""}`;

  try {
    const res  = await appFetch(`${STREAM_API}/api/imdb?id=${id}&action=stream`);
    const data = await res.json();
    
    updateServerTabs(data, id);
    renderPlayerEpisodeSection();
    
    if (data.stream_url) {
      playHls(data.stream_url, {alternateStreamEndpoint: `${STREAM_API}/api/get-video?id=${encodeURIComponent(id)}`, forceHls:true});
      autoLoadFirstSubtitle();
    } else if (playerSources.embed) {
      selectServerSource("embed");
    } else {
      showPError("Stream tidak tersedia untuk film ini.");
    }
  } catch(e) {
    showPError("Gagal mengambil stream: " + e.message);
  }
}

async function startTvEpisodeStream(season, episode) {
  if (!selectedItem) return;
  selectedItem = await ensureTmdbMeta(selectedItem);
  const tmdbId = selectedItem._tmdb_id;
  const title = selectedItem["#TITLE"] || "Serial";
  if (!tmdbId) { alert("TMDB ID tidak tersedia untuk serial ini."); return; }

  currentPlayback = {mediaType:"tv", tmdbId, season, episode, totalEpisodes:null, title};
  SUB.imdbId       = selectedItem["#IMDB_ID"] || null;
  SUB.tmdbId       = tmdbId;
  SUB.title        = title;
  SUB.mediaType    = "tv";
  SUB.season       = season;
  SUB.episode      = episode;
  SUB.activeFileId = null;
  SUB.activeLang   = localStorage.getItem("preferred_lang") || "id";
  resetSubtitlePanel();

  enterBrowseOrientationMode();
  closeDetail();
  
  updatePlayerMetadata();
  document.getElementById("playerMode").style.display = "block";
  document.getElementById("episodeNav").style.display = "flex";
  updateEpisodeLabel();
  showPLoading(`Mengambil stream ${title} S${season}E${episode}...`);
  document.getElementById("playerTitle").textContent = `${title} - S${season}E${episode}`;

  try {
    const imdbId = selectedItem["#IMDB_ID"] || "";
    const q = imdbId
      ? new URLSearchParams({id: imdbId, action: "stream", s: season, e: episode})
      : new URLSearchParams({id: tmdbId, type: "tv", s: season, e: episode});
    const endpoint = imdbId ? "imdb" : "tmdb-stream";
    const res = await appFetch(`${STREAM_API}/api/${endpoint}?${q}`);
    const data = await res.json();
    
    if (data.totalEpisodes) currentPlayback.totalEpisodes = data.totalEpisodes;
    if (data.imdbId || data.imdb_id) SUB.imdbId = data.imdbId || data.imdb_id;
    
    updateServerTabs(data, SUB.imdbId || imdbId, season, episode);
    renderPlayerEpisodeSection();
    
    if (data.episodeTitle) {
      document.getElementById("playerTitle").textContent = `${title} - ${data.episodeTitle} (S${season}E${episode})`;
      document.getElementById("playerPageSubtitle").textContent = `S${season}E${episode} · ${data.episodeTitle}`;
    } else {
      document.getElementById("playerPageSubtitle").textContent = `S${season}E${episode}`;
    }
    
    const streamUrl = data.stream_url || data.streamUrl || data.link;
    if (streamUrl) {
      const altEndpoint = SUB.imdbId ? `${STREAM_API}/api/get-video?id=${encodeURIComponent(SUB.imdbId)}` : "";
      playHls(streamUrl, {alternateStreamEndpoint: altEndpoint, forceHls:true});
      autoLoadFirstSubtitle();
    } else if (playerSources.embed) {
      selectServerSource("embed");
    } else {
      showPError(data.message || "Stream episode tidak tersedia.");
    }
  } catch(e) {
    showPError("Gagal mengambil episode: " + e.message);
  }
}

function updateEpisodeLabel() {
  document.getElementById("episodeLabel").textContent = `S${currentPlayback.season}E${currentPlayback.episode}`;
}

function changeEpisode(delta) {
  if (currentPlayback.mediaType !== "tv") return;
  const next = currentPlayback.episode + delta;
  if (next < 1) return;
  if (currentPlayback.totalEpisodes && next > currentPlayback.totalEpisodes) return;
  startTvEpisodeStream(currentPlayback.season, next);
}

/* ═══════════════════════════════════════
   PLAYER — HLS + PLYR
═══════════════════════════════════════ */
function updateQuality(q) {
  if (!hlsInst) return;
  hlsInst.levels.forEach((level, i) => { if (level.height===q) hlsInst.currentLevel=i; });
}

function getDirectStreamFallback(streamUrl) {
  try {
    const url = new URL(streamUrl, window.location.href);
    if (!url.pathname.endsWith("/api/proxy")) return "";
    return url.searchParams.get("url") || "";
  } catch {
    const m = String(streamUrl).match(/[?&]url=([^&]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }
}

async function signProxyUrl(url) {
  if (!url || !/^https?:\/\//i.test(url)) return "";
  const res = await appFetch(`${STREAM_API}/api/proxy/sign?url=${encodeURIComponent(url)}`);
  const data = await res.json().catch(() => ({}));
  return res.ok ? (data.url || "") : "";
}

async function normalizeStreamUrl(url) {
  if (!url) return "";
  if (/^https?:\/\//i.test(url) && url.toLowerCase().includes(".m3u8") && !url.includes("/api/proxy?url=")) {
    return await signProxyUrl(url);
  }
  return url;
}

async function getFallbackProxyUrl(streamUrl) {
  const rawUrl = getDirectStreamFallback(streamUrl) || streamUrl;
  if (!STREAM_PROXY_FALLBACK || !rawUrl || !/^https?:\/\//i.test(rawUrl)) return "";
  if (rawUrl.includes(`${STREAM_PROXY_FALLBACK}/api/proxy?url=`)) return "";
  return await signProxyUrl(rawUrl);
}

async function tryAlternateStream(opts) {
  if (!opts.alternateStreamEndpoint || opts.alternateTried) return false;
  try {
    showPLoading("Mencoba sumber stream alternatif...");
    const res = await appFetch(opts.alternateStreamEndpoint);
    const data = await res.json();
    const altUrl = await normalizeStreamUrl(data.stream_url || data.streamUrl || data.link || data.url || "");
    if (!res.ok || !altUrl) return false;
    playHls(altUrl, {...opts, alternateTried:true, skipDirectFallback:false, forceHls:true});
    return true;
  } catch {
    return false;
  }
}

/* ── PLAYER UTILITIES (Toast, Time Formatting, Key shortcuts, Gestures) ── */
function formatTime(sec) {
  if (isNaN(sec) || !isFinite(sec)) return "00:00";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  }
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

function showToast(msg) {
  let t = document.getElementById("plyrToast");
  if (!t) {
    t = document.createElement("div");
    t.id = "plyrToast";
    t.className = "toast-container";
    document.body.appendChild(t);
  }
  t.innerText = msg;
  t.classList.add("show");
  clearTimeout(t.timer);
  t.timer = setTimeout(() => t.classList.remove("show"), 2500);
}

// Keyboard shortcuts for desktop users
document.addEventListener("keydown", (e) => {
  const pMode = document.getElementById("playerMode");
  if (!pMode || pMode.style.display === "none") return;
  if (document.activeElement && ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
  
  const v = document.getElementById("mainVideo");
  if (!v || !plyrInst) return;

  switch(e.key.toLowerCase()) {
    case " ":
      e.preventDefault();
      plyrInst.togglePlay();
      break;
    case "arrowleft":
      e.preventDefault();
      v.currentTime = Math.max(0, v.currentTime - 10);
      showToast("⏪ -10s");
      break;
    case "arrowright":
      e.preventDefault();
      v.currentTime = Math.min(v.duration || 0, v.currentTime + 10);
      showToast("⏩ +10s");
      break;
    case "arrowup":
      e.preventDefault();
      plyrInst.volume = Math.min(1, plyrInst.volume + 0.1);
      showToast(`Volume: ${Math.round(plyrInst.volume * 100)}%`);
      break;
    case "arrowdown":
      e.preventDefault();
      plyrInst.volume = Math.max(0, plyrInst.volume - 0.1);
      showToast(`Volume: ${Math.round(plyrInst.volume * 100)}%`);
      break;
    case "f":
      e.preventDefault();
      plyrInst.fullscreen.toggle();
      break;
    case "m":
      e.preventDefault();
      plyrInst.muted = !plyrInst.muted;
      showToast(plyrInst.muted ? "Muted" : "Unmuted");
      break;
  }
});

// Double tap gesture for seeking (left/right screen tap)
let lastTapTime = 0;
let singleClickTimeout = null;
document.addEventListener("DOMContentLoaded", () => {
  const container = document.getElementById("videoContainer");
  if (container) {
    container.addEventListener("click", (e) => {
      if (e.target.closest(".plyr__controls")) return;

      const now = Date.now();
      const rect = container.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const width = rect.width;

      if (now - lastTapTime < 300) {
        if (singleClickTimeout) {
          clearTimeout(singleClickTimeout);
          singleClickTimeout = null;
        }
        const v = document.getElementById("mainVideo");
        if (v && isFinite(v.duration)) {
          if (x < width * 0.35) {
            v.currentTime = Math.max(0, v.currentTime - 10);
            showToast("⏪ -10s");
          } else if (x > width * 0.65) {
            v.currentTime = Math.min(v.duration, v.currentTime + 10);
            showToast("⏩ +10s");
          }
        }
        lastTapTime = 0;
      } else {
        lastTapTime = now;
        singleClickTimeout = setTimeout(() => {
          if (plyrInst) {
            plyrInst.togglePlay();
          }
          singleClickTimeout = null;
        }, 280);
      }
    });
  }
});

function playHls(streamUrl, opts = {}) {
  const v  = document.getElementById("mainVideo");
  const vc = document.getElementById("videoContainer");

  if (hlsInst)  { hlsInst.destroy();  hlsInst  = null; }
  if (plyrInst) { plyrInst.destroy(); plyrInst = null; }
  v.ontimeupdate = () => saveFilmContinueProgress(v);
  v.onended = () => {
    saveFilmContinueProgress(v);
    if (currentPlayback.mediaType === "tv") changeEpisode(1);
  };
  v.onloadedmetadata = () => {
    try {
      if (!selectedItem) return;
      const id = `film_${currentPlayback.mediaType}_${selectedItem["#IMDB_ID"] || selectedItem._tmdb_id || currentPlayback.title}`;
      const all = JSON.parse(localStorage.getItem('cw_progress') || '[]');
      const found = all.find(x => x.id === id);
      if (found && found.pct > 0.02 && found.pct < 0.98) {
        const seekTime = found.pct * v.duration;
        if (isFinite(seekTime) && seekTime > 0) {
          v.currentTime = seekTime;
          showToast(`Melanjutkan dari ${formatTime(seekTime)}`);
        }
      }
    } catch(e) {
      console.error(e);
    }
  };

  const isM3u8 = opts.forceHls || streamUrl.toLowerCase().includes(".m3u8") || streamUrl.includes("mpegurl");
  const plyrOpts = {
    controls: ["play-large","play","progress","current-time","mute","volume","captions","settings","pip","airplay","fullscreen"],
    settings: ["captions","quality","speed"],
    captions: {active:true, update:true},
    clickToPlay: false,
  };

  if (isM3u8 && Hls.isSupported()) {
    hlsInst = new Hls({
      maxBufferLength: 30,
      manifestLoadingTimeOut: 10000,
      levelLoadingTimeOut: 10000,
      xhrSetup: function(xhr, url) {
        if (url && (url.includes("tmstrd.justhd.tv") || url.includes("leadgenerationblueprint.site"))) {
          xhr.setRequestHeader("Origin", "https://brightpathsignals.com");
        }
      },
      fetchSetup: function(context, initParams) {
        if (context.url && (context.url.includes("tmstrd.justhd.tv") || context.url.includes("leadgenerationblueprint.site"))) {
          initParams.headers = Object.assign(initParams.headers || {}, {"Origin": "https://brightpathsignals.com", "Referer": "https://brightpathsignals.com/"});
        }
        return new Request(context.url, initParams);
      },
    });
    hlsInst.loadSource(streamUrl);
    hlsInst.attachMedia(v);
    hlsInst.on(Hls.Events.MANIFEST_PARSED, () => {
      hidePOverlays();
      vc.style.display = "block";
      const qs = hlsInst.levels.map(l=>l.height).filter((h,i,a)=>h&&a.indexOf(h)===i).sort((a,b)=>b-a);
      if (qs.length>1) plyrOpts.quality = {default:qs[0],options:qs,forced:true,onChange:updateQuality};
      plyrInst = new Plyr(v, plyrOpts);
      if (plyrInst.eventListeners) {
        plyrInst.eventListeners.forEach(el => {
          if (el.type === "dblclick" && el.element) {
            el.element.removeEventListener(el.type, el.callback, el.options);
          }
        });
      }

      /* ── FULLSCREEN → AUTO LANDSCAPE ──
         Plyr enterfullscreen event */
      plyrInst.on("enterfullscreen", onEnterFullscreen);
      plyrInst.on("exitfullscreen",  onExitFullscreen);

      v.play().catch(()=>{});
    });
    hlsInst.on(Hls.Events.ERROR, async (e, d) => {
      if (d.fatal) {
        if(hlsInst){hlsInst.destroy();hlsInst=null;}
        const directUrl = opts.skipDirectFallback ? "" : getDirectStreamFallback(streamUrl);
        if (directUrl && directUrl !== streamUrl) {
          showPLoading("Proxy diblokir, mencoba stream langsung...");
          playHls(directUrl, {...opts, skipDirectFallback:true});
          return;
        }
        const fallbackProxyUrl = opts.skipProxyFallback ? "" : await getFallbackProxyUrl(streamUrl);
        if (fallbackProxyUrl) {
          showPLoading("Proxy utama diblokir, mencoba proxy cadangan...");
          playHls(fallbackProxyUrl, {...opts, skipProxyFallback:true, skipDirectFallback:false});
          return;
        }
        if (opts.alternateStreamEndpoint && !opts.alternateTried) {
          tryAlternateStream(opts).then((started) => {
            if (!started) showPError("Stream gagal dimuat.");
          });
          return;
        }
        showPError("Stream gagal dimuat.");
      }
    });
  } else {
    v.src = streamUrl;
    plyrInst = new Plyr(v, plyrOpts);
    if (plyrInst.eventListeners) {
      plyrInst.eventListeners.forEach(el => {
        if (el.type === "dblclick" && el.element) {
          el.element.removeEventListener(el.type, el.callback, el.options);
        }
      });
    }
    plyrInst.on("enterfullscreen", onEnterFullscreen);
    plyrInst.on("exitfullscreen",  onExitFullscreen);
    v.addEventListener("loadedmetadata", () => { hidePOverlays(); vc.style.display="block"; v.play().catch(()=>{}); }, {once:true});
    v.addEventListener("error", () => showPError("Gagal memutar stream."), {once:true});
  }

  /* Native fullscreen change (fallback untuk browser yang tidak pakai Plyr fullscreen) */
  document.addEventListener("fullscreenchange", onNativeFullscreenChange);
  document.addEventListener("webkitfullscreenchange", onNativeFullscreenChange);
}

/* ═══════════════════════════════════════
   FULLSCREEN → AUTO ROTATE LANDSCAPE
═══════════════════════════════════════ */
async function onEnterFullscreen() {
  await enterWatchingOrientationMode();
}

async function onExitFullscreen() {
  await enterBrowseOrientationMode();
}

function onNativeFullscreenChange() {
  const isFs = !!(
    document.fullscreenElement ||
    document.webkitFullscreenElement ||
    document.mozFullScreenElement
  );
  if (isFs) {
    enterWatchingOrientationMode();
  } else {
    enterBrowseOrientationMode();
  }
}

async function requestLandscape() {
  try {
    await OrientationLock.lockLandscape();
  } catch(e) {
    /* Beberapa browser membutuhkan user gesture atau tidak mendukung */
    console.warn("Orientation lock tidak didukung:", e.message);
  }
}

function setRotateButtonState(locked, orientation = null) {
  const btn = document.getElementById("btnRotateLock");
  if (!btn) return;
  const label = document.getElementById("rotateLockLabel");
  const icon = btn.querySelector(".lock-icon");
  btn.classList.toggle("active", locked);
  if (icon) icon.textContent = locked ? "🔒" : "🔄";
  if (label) {
    label.textContent = locked
      ? (orientation === "landscape" ? "LANDSCAPE" : "PORTRAIT")
      : (orientationMode === "player" ? "AUTO" : "ROTATE");
  }
}

async function enterBrowseOrientationMode() {
  orientationMode = "browse";
  lockOrientation = "portrait";
  rotateLocked = true;
  setRotateButtonState(true, "portrait");
  await OrientationLock.lockPortrait();
}

async function enterWatchingOrientationMode() {
  orientationMode = "player";
  rotateLocked = true;
  lockOrientation = "landscape";
  setRotateButtonState(true, "landscape");
  await OrientationLock.lockLandscape();
}

/* ═══════════════════════════════════════
   ROTATE LOCK BUTTON
═══════════════════════════════════════ */
/* ── Deteksi orientasi berubah → enforce lock ── */
function toggleRotateLock() {
  if (orientationMode === "browse") {
    enterBrowseOrientationMode();
    return;
  }
  if (!rotateLocked) {
    const currentOri = window.screen?.orientation?.type || "";
    const isLandscape = currentOri.startsWith("landscape") ||
                        (window.innerWidth > window.innerHeight);
    lockOrientation = isLandscape ? "landscape" : "portrait";
    try {
      if (OrientationLock.supported()) {
        OrientationLock.lock(lockOrientation).then(() => {
          rotateLocked = true;
          setRotateButtonState(true, lockOrientation);
        }).catch(() => showRotateLockFallback(isLandscape));
      } else {
        showRotateLockFallback(isLandscape);
      }
    } catch {
      showRotateLockFallback(isLandscape);
    }
  } else {
    rotateLocked = false;
    lockOrientation = null;
    OrientationLock.unlock();
    setRotateButtonState(false);
  }
}

function showRotateLockFallback(isLandscape) {
  rotateLocked = true;
  lockOrientation = isLandscape ? "landscape" : "portrait";
  setRotateButtonState(true, lockOrientation);
}

window.addEventListener("orientationchange", () => {
  if (!rotateLocked || !lockOrientation) return;
  /* Re-apply lock jika berubah */
  setTimeout(async () => {
    try {
      await OrientationLock.lock(lockOrientation);
    } catch {}
  }, 100);
});

/* ═══════════════════════════════════════
   PLAYER HELPERS
═══════════════════════════════════════ */
function showPError(msg) {
  hidePOverlays();
  document.getElementById("videoContainer").style.display = "none";
  document.getElementById("pErrTxt").textContent = msg;
  document.getElementById("pError").classList.remove("hidden");
}

function showPLoading(txt) {
  document.getElementById("pError").classList.add("hidden");
  document.getElementById("pLoading").classList.remove("hidden");
  document.getElementById("pLoadTxt").textContent = txt;
  document.getElementById("videoContainer").style.display   = "none";
  document.getElementById("playerFrame").style.display = "none";
}

function hidePOverlays() {
  document.getElementById("pLoading").classList.add("hidden");
  document.getElementById("pError").classList.add("hidden");
}

function closePlayer() {
  document.getElementById("playerMode").style.display = "none";
  const v  = document.getElementById("mainVideo");
  const vc = document.getElementById("videoContainer");
  v.pause(); v.src = ""; vc.style.display = "none";
  Array.from(v.querySelectorAll("track")).forEach(t => t.remove());
  const f = document.getElementById("playerFrame");
  f.src = ""; f.style.display = "none";
  if (hlsInst)  { hlsInst.destroy();  hlsInst  = null; }
  if (plyrInst) { plyrInst.destroy(); plyrInst = null; }
  document.getElementById("pLoading").classList.remove("hidden");
  document.getElementById("pError").classList.add("hidden");
  document.getElementById("subPanelBrowse").classList.remove("visible");
  const bd2 = document.getElementById("subBackdrop");
  if (bd2) bd2.classList.remove("visible");
  clearTimeout(barTimer);
  stopSubOverlay(); _subCues = [];

  /* Reset subtitle UI */
  SUB.activeFileId = null;
  const btn = document.getElementById("btnSubBrowse");
  btn.textContent = "CC SUB"; btn.classList.remove("active");
  document.getElementById("subDot").classList.remove("active");
  document.getElementById("subStatus").textContent = "Tidak ada subtitle aktif";
  document.getElementById("episodeNav").style.display = "none";
  currentPlayback = {mediaType:"movie", tmdbId:null, season:1, episode:1, totalEpisodes:null, title:""};

  /* Reset rotate lock */
  enterBrowseOrientationMode();

  /* Lepas event listener native fullscreen */
  document.removeEventListener("fullscreenchange", onNativeFullscreenChange);
  document.removeEventListener("webkitfullscreenchange", onNativeFullscreenChange);
}

/* Auto-hide player bar */
document.getElementById("playerMode").addEventListener("mousemove", () => {
  document.getElementById("playerBar").classList.remove("faded");
  clearTimeout(barTimer);
  barTimer = setTimeout(() =>
    document.getElementById("playerBar").classList.add("faded"), 3500);
});

/* ═══════════════════════════════════════
   SUBTITLE SYSTEM
   ═══════════════════════════════════════ */
async function autoLoadFirstSubtitle() {
  const lang = SUB.activeLang || "id";
  const list = document.getElementById("subList");
  if (list) {
    list.innerHTML = `<div class="sub-searching"><div class="spinner"></div> Mencari ${lang.toUpperCase()}...</div>`;
  }
  try {
    let q = "";
    if (SUB.mediaType === "tv" && SUB.tmdbId) {
      q = `tmdb_id=${encodeURIComponent(SUB.tmdbId)}&type=tv&season=${encodeURIComponent(SUB.season)}&episode=${encodeURIComponent(SUB.episode)}&lang=${lang}`;
    }
    else if (SUB.imdbId) {
      q = `imdb_id=${encodeURIComponent(SUB.imdbId)}&type=${encodeURIComponent(SUB.mediaType || "movie")}&lang=${lang}`;
    }
    else if (SUB.title) {
      q = `query=${encodeURIComponent(SUB.title)}&lang=${lang}`;
    }
    else {
      if (list) list.innerHTML = `<div class="sub-empty">Tidak ada ID film</div>`;
      return;
    }

    const r = await subtitleFetch(`${STREAM_API}/api/subtitle/search?${q}`);
    const d = await r.json();
    if (!r.ok || d.status === "error") {
      if (list) list.innerHTML = `<div class="sub-empty">⚠ ${esc(d.error || "API error")}</div>`;
      return;
    }

    const items = (d.data || []).slice(0, 12);
    if (items.length > 0) {
      const bestSub = items[0];
      const subRes = await subtitleFetch(`${STREAM_API}/api/subtitle/download?file_id=${bestSub.file_id}`);
      if (subRes.ok) {
        const srt = await subRes.text();
        if (srt && srt.trim().length > 10) {
          _subCues = parseSrt(srt);
          const vtt = srtToVtt(srt);
          const blob = new Blob([vtt], {type: "text/vtt"});
          const url = URL.createObjectURL(blob);
          applyVttToVideo(url, lang);
          
          SUB.activeFileId = bestSub.file_id;
          
          const btn = document.getElementById("btnSubBrowse");
          if (btn) {
            btn.textContent = `CC: ${lang.toUpperCase()}`;
            btn.classList.add("active");
          }
          document.getElementById("subDot").classList.add("active");
          document.getElementById("subStatus").textContent = `Subtitle aktif: ${lang.toUpperCase()}`;
          showToast(`Subtitle ${lang.toUpperCase()} dimuat otomatis`);
        }
      }
      renderSubtitleList(items, lang);
    } else {
      if (list) list.innerHTML = `<div class="sub-empty">Tidak ada subtitle ${lang.toUpperCase()}</div>`;
    }
  } catch (e) {
    console.warn("Auto-load subtitle failed:", e);
    if (list) list.innerHTML = `<div class="sub-empty">Gagal mengambil subtitle</div>`;
  }
}

function resetSubtitlePanel() {
  SUB.activeFileId = null;
  stopSubOverlay();
  _subCues = [];
  document.getElementById("subPanelBrowse").classList.remove("visible");
  const bd = document.getElementById("subBackdrop");
  if (bd) bd.classList.remove("visible");
  
  const lang = SUB.activeLang || localStorage.getItem("preferred_lang") || "id";
  document.getElementById("subList").innerHTML = `<div class="sub-searching"><div class="spinner"></div> Mencari ${lang.toUpperCase()}...</div>`;
  
  document.getElementById("subDot").classList.remove("active");
  document.getElementById("subStatus").textContent = "Tidak ada subtitle aktif";
  const btn = document.getElementById("btnSubBrowse");
  if (btn) { btn.textContent = "CC SUB"; btn.classList.remove("active"); }
}

function toggleSubPanel() {
  const panel    = document.getElementById("subPanelBrowse");
  const backdrop = document.getElementById("subBackdrop");
  const open = panel.classList.contains("visible");
  if (!open) {
    panel.classList.add("visible");
    backdrop.classList.add("visible");
    buildLangRow();
    
    // Only auto-fetch if the list is currently showing an empty/error state or was reset
    const listEl = document.getElementById("subList");
    if (listEl.querySelector(".sub-empty")) {
      fetchSubtitles(SUB.activeLang);
    }
  } else {
    panel.classList.remove("visible");
    backdrop.classList.remove("visible");
  }
}

function buildLangRow() {
  document.getElementById("subLangRow").innerHTML = LANGS.map(l => `
    <button class="sub-lang-btn ${l.code===SUB.activeLang?"active":""}"
            onclick="selectLang('${l.code}',this)">${l.label}</button>
  `).join("");
}

function selectLang(code, el) {
  SUB.activeLang = code;
  localStorage.setItem("preferred_lang", code);
  document.querySelectorAll("#subLangRow .sub-lang-btn").forEach(b=>b.classList.remove("active"));
  el.classList.add("active");
  fetchSubtitles(code);
}

function renderSubtitleList(items, lang) {
  const list = document.getElementById("subList");
  if (!list) return;
  if (!items || !items.length) {
    list.innerHTML = `<div class="sub-empty">Tidak ada subtitle ${lang.toUpperCase()}</div>`;
    return;
  }
  list.innerHTML = items.map((s, i) => {
    const name = esc(s.release || s.file_name || `Subtitle ${i + 1}`);
    const dlc = s.downloads > 999 ? Math.round(s.downloads / 1000) + "k" : (s.downloads || "");
    return `
      <div class="sub-item ${s.file_id === SUB.activeFileId ? "active" : ""}" id="sub-b-${i}"
           onclick="loadSubtitle('${s.file_id}',${i})">
        <div class="sub-item-left">
          <div class="sub-item-name" title="${name}">${name}</div>
          <div class="sub-item-meta">
            ${s.hearing_impaired ? '<span class="sub-badge sub-badge-hi">♿ HI</span>' : ""}
            ${dlc ? `<span class="sub-badge sub-badge-dl">⬇ ${dlc}</span>` : ""}
            ${s.fps ? `<span class="sub-badge sub-badge-dl">${s.fps}fps</span>` : ""}
          </div>
        </div>
        <div class="sub-item-load">⟳</div>
      </div>`;
  }).join("");
}

async function fetchSubtitles(lang) {
  const list = document.getElementById("subList");
  list.innerHTML = `<div class="sub-searching"><div class="spinner"></div> Mencari ${lang.toUpperCase()}...</div>`;
  try {
    let q = "";
    if (SUB.mediaType === "tv" && SUB.tmdbId) {
      q = `tmdb_id=${encodeURIComponent(SUB.tmdbId)}&type=tv&season=${encodeURIComponent(SUB.season)}&episode=${encodeURIComponent(SUB.episode)}&lang=${lang}`;
    }
    else if (SUB.imdbId) q = `imdb_id=${encodeURIComponent(SUB.imdbId)}&type=${encodeURIComponent(SUB.mediaType || "movie")}&lang=${lang}`;
    else if (SUB.title) q = `query=${encodeURIComponent(SUB.title)}&lang=${lang}`;
    else { list.innerHTML = `<div class="sub-empty">Tidak ada ID film</div>`; return; }

    const r = await subtitleFetch(`${STREAM_API}/api/subtitle/search?${q}`);
    const d = await r.json();
    if (!r.ok || d.status==="error") { list.innerHTML=`<div class="sub-empty">⚠ ${esc(d.error||"API error")}</div>`; return; }

    const items = (d.data||[]).slice(0,12);
    renderSubtitleList(items, lang);
  } catch { list.innerHTML=`<div class="sub-empty">Gagal mengambil subtitle</div>`; }
}

async function loadSubtitle(fileId, idx) {
  document.querySelectorAll("#subList .sub-item").forEach(el=>el.classList.remove("active","loading"));
  const itemEl = document.getElementById(`sub-b-${idx}`);
  if (itemEl) itemEl.classList.add("loading");

  try {
    const r = await subtitleFetch(`${STREAM_API}/api/subtitle/download?file_id=${fileId}`);
    if (!r.ok) { const e=await r.json().catch(()=>({})); throw new Error(e.error||`HTTP ${r.status}`); }
    const srt = await r.text();
    if (!srt||srt.trim().length<10) throw new Error("File subtitle kosong");

    _subCues = parseSrt(srt);
    const vtt  = srtToVtt(srt);
    const blob = new Blob([vtt],{type:"text/vtt"});
    const url  = URL.createObjectURL(blob);
    applyVttToVideo(url, SUB.activeLang);

    SUB.activeFileId = fileId;
    if (itemEl) { itemEl.classList.remove("loading"); itemEl.classList.add("active"); }

    const releaseName = itemEl?.querySelector(".sub-item-name")?.textContent?.trim() || "Subtitle";
    updateSubStatus(true, releaseName);
    const btn = document.getElementById("btnSubBrowse");
    btn.textContent = "CC ✓"; btn.classList.add("active");
  } catch(e) {
    if (itemEl) itemEl.classList.remove("loading");
    updateSubStatus(false, "Gagal: "+e.message);
  }
}

const _isIframeMode = () => document.getElementById("playerFrame").style.display==="block";

function applyVttToVideo(vttUrl, lang) {
  if (_isIframeMode()) { startSubOverlay(); return; }
  const video = document.getElementById("mainVideo");
  Array.from(video.querySelectorAll("track")).forEach(t=>t.remove());
  const track = document.createElement("track");
  track.kind="subtitles"; track.src=vttUrl;
  track.srclang=lang; track.label=lang.toUpperCase(); track.default=true;
  video.appendChild(track);

  /* Sync ke Plyr */
  if (plyrInst) {
    setTimeout(() => {
      try {
        plyrInst.currentTrack = 0;
        if (!plyrInst.captions.active) plyrInst.toggleCaptions(true);
      } catch {
        fallbackActivateTrack(video, lang);
      }
    }, 300);
  } else {
    setTimeout(() => fallbackActivateTrack(video, lang), 200);
  }
}

function fallbackActivateTrack(video, lang) {
  Array.from(video.textTracks).forEach(t => { t.mode="hidden"; });
  const target = Array.from(video.textTracks).find(t=>t.language===lang||t.label===lang.toUpperCase());
  if (target) target.mode="showing";
  else if (video.textTracks[0]) video.textTracks[0].mode="showing";
}

function parseSrt(srt) {
  return srt.replace(/\r\n/g,"\n").replace(/\r/g,"\n").trim().split(/\n\n+/).map(block => {
    const lines = block.split("\n");
    const tl = lines.find(l=>l.includes("-->"));
    if (!tl) return null;
    const [s,e] = tl.split("-->").map(x => {
      const p = x.trim().replace(",",".").split(":");
      return (+p[0])*3600+(+p[1])*60+parseFloat(p[2]);
    });
    const text = lines.slice(lines.indexOf(tl)+1).join("\n").replace(/<[^>]+>/g,"").trim();
    return text ? {start:s,end:e,text} : null;
  }).filter(Boolean);
}

function startSubOverlay() {
  stopSubOverlay();
  const overlay = document.getElementById("subOverlay");
  if (!overlay||_subCues.length===0) return;
  overlay.style.display = "block";
  const t0 = Date.now()/1000;
  _subTimer = setInterval(() => {
    const now = Date.now()/1000 - t0;
    const cue = _subCues.find(c=>now>=c.start&&now<c.end);
    overlay.innerHTML = "";
    if (cue) {
      const span = document.createElement("span");
      span.textContent = cue.text;
      overlay.appendChild(span);
    }
  }, 200);
}

function stopSubOverlay() {
  if (_subTimer) { clearInterval(_subTimer); _subTimer=null; }
  const o = document.getElementById("subOverlay");
  if (o) { o.innerHTML=""; o.style.display="none"; }
}

function disableSubtitle() {
  const video = document.getElementById("mainVideo");
  Array.from(video.querySelectorAll("track")).forEach(t=>t.remove());
  Array.from(video.textTracks).forEach(t=>{t.mode="disabled";});
  if (plyrInst) {
    try { plyrInst.currentTrack=-1; if(plyrInst.captions.active)plyrInst.toggleCaptions(false); } catch {}
  }
  stopSubOverlay(); _subCues=[];
  SUB.activeFileId=null;
  document.querySelectorAll("#subList .sub-item").forEach(el=>el.classList.remove("active"));
  updateSubStatus(false,"Tidak ada subtitle aktif");
  const btn=document.getElementById("btnSubBrowse");
  btn.textContent="CC SUB"; btn.classList.remove("active");
}

function updateSubStatus(active,text) {
  document.getElementById("subDot").classList.toggle("active",active);
  document.getElementById("subStatus").textContent=text;
}

function srtToVtt(srt) {
  return "WEBVTT\n\n"+srt
    .replace(/\r\n/g,"\n").replace(/\r/g,"\n")
    .replace(/^\uFEFF/,"")
    .replace(/(\d{2}:\d{2}:\d{2}),(\d{3})/g,"$1.$2")
    .replace(/<[^>]+>/g,"")
    .trim();
}

/* ─── Util ─── */
function esc(s) {
  return String(s||"").replace(/[&<>"']/g,m=>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[m]
  );
}
</script>


/* ════════════════════════════════════════
   DRACIN TAB HANDLER (FINAL FIX)
════════════════════════════════════════ */
const _initialMainTab = new URLSearchParams(location.search).get('tab') === 'dracin' ? 'dracin' : 'film';
let _mainTab = _initialMainTab;

function switchMainTab(tab) {
  _mainTab = tab;
  renderContinueWatching();

  document.querySelectorAll('.ntab').forEach(t => t.classList.remove('active'));
  document.getElementById(tab === 'film' ? 'ntFilm' : 'ntDracin').classList.add('active');

  const isFilm = tab === 'film';

  // Toggle visibility — dracin-section sekarang SEJAJAR dengan gridWrap (bukan di dalamnya)
  document.getElementById('gridWrap').style.display     = isFilm ? '' : 'none';
  document.getElementById('dracin-section').style.display = isFilm ? 'none' : 'flex';

  // Sembunyikan elemen khusus film saat tab dracin aktif
  const searchArea = document.querySelector('.search-area');
  const filterWrap = document.querySelector('.filter-wrap');
  if (searchArea) searchArea.style.display = isFilm ? '' : 'none';
  if (filterWrap) filterWrap.style.display = isFilm ? '' : 'none';

  if (tab === 'dracin' && !DR.initialized) {
    DR.initialized = true;
    console.log('%c[Dracin] Inisialisasi...', 'color:#6fb6b0');
    setTimeout(() => {
      dracinLoadRank();
      dracinLoadBrowse(true);
    }, 50);
  }
}

/* DRACIN STATE */
const DR = {
  platform: 'all',
  browseMode: 'home',
  searchQuery: '',
  page: 1,
  pageSize: 10,
  hasMore: false,
  loading: false,
  initialized: false,
  fetchCache: new Map(),
  inflight: new Map(),
  seenBookKeys: new Set(),
  browseController: null,
  browseSeq: 0,
  rankSeq: 0,
};

function esc(s) {
  return String(s || "").replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[m]);
}

function dracinStatus(show, txt) {
  const el = document.getElementById('dracinStatus');
  el.style.display = show ? 'flex' : 'none';
  if (txt) document.getElementById('dracinStatusTxt').textContent = txt;
}

function dracinMakeCard(book, rank = 0, forScroll = false) {
  const a = document.createElement('a');
  a.className = 'dr-card';
  if (forScroll) a.style.width = '110px';

  const sourcePlatform = book.platform || DR.platform;
  const href = `/dracin-player.html?id=${encodeURIComponent(book.bookId)}&platform=${encodeURIComponent(sourcePlatform)}&cover=${encodeURIComponent(book.cover||'')}`;
  a.href = href;
  a.onclick = e => {
    e.preventDefault();
    openDracinDetail({...book, platform: sourcePlatform, href});
  };

  let views = '';
  if (book.playCount) {
    const num = typeof book.playCount === 'string' 
      ? parseFloat(book.playCount.replace(/[^0-9.]/g, '')) || 0 
      : book.playCount;
    views = num > 1000000 ? (num/1000000).toFixed(1)+'M' : num > 1000 ? Math.round(num/1000)+'K' : num;
  }
  const eps = book.chapterCount ? `${book.chapterCount} Ep` : '';

  a.innerHTML = `
    <div class="dr-card-poster">
      <img src="${esc(book.cover||'')}" loading="lazy" alt="${esc(book.bookName)}"
           referrerpolicy="no-referrer"
           onerror="this.style.display='none'; this.parentNode.style.background='#12161b'; this.parentNode.innerHTML='NO IMAGE';">
      ${rank > 0 ? `<div class="dr-card-rank">${rank}</div>` : ''}
      <div class="dr-card-poster-overlay">
        <div class="dr-card-eps">${eps}</div>
      </div>
    </div>
    <div class="dr-card-info">
      <div class="dr-card-name">${esc(book.bookName || 'Untitled')}</div>
      <div class="dr-card-meta">${views}</div>
    </div>
  `;
  return a;
}

function dracinBookKey(book, fallbackPlatform = DR.platform) {
  if (!book || typeof book !== 'object') return '';
  const platform = String(book.platform || fallbackPlatform || '').trim().toLowerCase();
  const id = book.bookId || book.id || book.videoid || book.videoId || book.bookName || book.title || '';
  return `${platform}:${String(id).trim().toLowerCase()}`;
}

function dracinDedupeBooks(books, seen = new Set()) {
  const out = [];
  (books || []).forEach(book => {
    const key = dracinBookKey(book);
    if (!key || seen.has(key)) return;
    seen.add(key);
    out.push(book);
  });
  return out;
}

function getContinueItems() {
  try {
    const rows = JSON.parse(localStorage.getItem('cw_progress') || '[]');
    return Array.isArray(rows) ? rows.filter(Boolean) : [];
  } catch {
    return [];
  }
}

function continueHref(item) {
  if (item.mediaType === 'dracin') {
    return `/dracin-player.html?id=${encodeURIComponent(item.bookId || '')}&platform=${encodeURIComponent(item.platform || 'dramabox')}&ep=${encodeURIComponent(item.epIdx || 0)}&cover=${encodeURIComponent(item.poster || '')}`;
  }
  return item.href || '#';
}

function renderContinueList(sectionId, rowId, badgeId, items) {
  const section = document.getElementById(sectionId);
  const row = document.getElementById(rowId);
  const badge = document.getElementById(badgeId);
  if (!section || !row || !badge) return;
  if (!items.length) {
    section.style.display = 'none';
    row.innerHTML = '';
    badge.textContent = '';
    return;
  }
  section.style.display = 'block';
  badge.textContent = `${items.length} item`;
  row.innerHTML = items.slice(0, 12).map(item => {
    const pct = Math.max(6, Math.min(100, Math.round((item.pct || 0) * 100 || 8)));
    const meta = item.mediaType === 'dracin'
      ? `${(item.platform || 'dracin').toUpperCase()} · Ep ${item.epNum || (item.epIdx + 1) || 1}`
      : (item.epTitle || item.mediaType || 'Film');
    const action = item.mediaType === 'dracin'
      ? `location.href='${esc(continueHref(item))}'`
      : `openDetail(JSON.parse(this.dataset.item))`;
    const dataItem = esc(JSON.stringify(item.item || {
      "#TITLE": item.title || 'Untitled',
      "#IMG_POSTER": item.poster || '',
      "#TYPE": item.mediaType === 'tv' ? 'tvseries' : 'movie'
    }));
    return `
      <div class="cw-card" onclick="${action}" data-item="${dataItem}">
        <div class="cw-poster">
          ${item.poster ? `<img src="${esc(item.poster)}" alt="${esc(item.title || '')}" loading="lazy" onerror="this.style.display='none'">` : ''}
        </div>
        <div>
          <div class="cw-name">${esc(item.title || 'Untitled')}</div>
          <div class="cw-meta">${esc(meta)}</div>
          <div class="cw-progress" style="--pct:${pct}%"><span></span></div>
          <div class="cw-action">LANJUTKAN</div>
        </div>
      </div>`;
  }).join('');
}

function renderContinueWatching() {
  const items = getContinueItems();
  renderContinueList(
    'dracinContinueSection',
    'dracinContinueRow',
    'dracinContinueBadge',
    items.filter(item => item.mediaType === 'dracin')
  );
  renderContinueList(
    'filmContinueSection',
    'filmContinueRow',
    'filmContinueBadge',
    items.filter(item => item.mediaType !== 'dracin')
  );
}

let selectedDracinBook = null;
let selectedDracinEpisodes = [];

function dracinPlayerHref(book, epIdx = 0) {
  return `/dracin-player.html?id=${encodeURIComponent(book.bookId || '')}&platform=${encodeURIComponent(book.platform || DR.platform || 'dramabox')}&ep=${encodeURIComponent(epIdx)}&cover=${encodeURIComponent(book.cover || '')}`;
}

async function openDracinDetail(book) {
  selectedDracinBook = book;
  selectedDracinEpisodes = [];
  document.getElementById('drdTitle').textContent = book.bookName || 'Drama';
  document.getElementById('drdPoster').src = book.cover || '';
  document.getElementById('drdDesc').textContent = book.introduction || 'Sinopsis belum tersedia.';
  const tags = book.tags || book.tagNames || [];
  document.getElementById('drdMeta').innerHTML = [
    book.platform || DR.platform,
    book.chapterCount ? `${book.chapterCount} Ep` : '',
    book.playCount || '',
    ...tags.slice(0, 4)
  ].filter(Boolean).map(x => `<span class="dracin-pill">${esc(String(x))}</span>`).join('');
  document.getElementById('drdEpisodes').innerHTML = `<div class="episode-empty">Memuat episode...</div>`;
  document.getElementById('dracinDetailPanel').style.display = 'block';

  try {
    const payload = await dracinFetchPayload(`/api/dracin/episodes?id=${encodeURIComponent(book.bookId)}&platform=${encodeURIComponent(book.platform || DR.platform)}&lang=in`, { ttl: 120000 });
    selectedDracinEpisodes = payload?.episodes || [];
    renderDracinEpisodePreview();
  } catch {
    document.getElementById('drdEpisodes').innerHTML = `<div class="episode-empty">Episode belum bisa dimuat.</div>`;
  }
}

function renderDracinEpisodePreview() {
  const eps = selectedDracinEpisodes.slice(0, 24);
  if (!eps.length) {
    document.getElementById('drdEpisodes').innerHTML = `<button onclick="playSelectedDracin()">1</button>`;
    return;
  }
  document.getElementById('drdEpisodes').innerHTML = eps.map((ep, i) => {
    const n = ep.episode || ep.chapterIndex + 1 || i + 1;
    return `<button onclick="playSelectedDracin(${i})">${esc(n)}</button>`;
  }).join('');
}

function closeDracinDetail() {
  document.getElementById('dracinDetailPanel').style.display = 'none';
}

function playSelectedDracin(epIdx = 0) {
  if (!selectedDracinBook) return;
  location.href = dracinPlayerHref(selectedDracinBook, epIdx);
}

function dracinSkeletons(container, n = 8, isRow = false) {
  container.innerHTML = '';
  for (let i = 0; i < n; i++) {
    const s = document.createElement('div');
    s.className = 'dr-skel';
    if (isRow) s.style.cssText = 'flex-shrink:0;width:110px';
    s.innerHTML = `<div class="dr-skel-poster"></div><div class="dr-skel-info"><div class="dr-skel-line"></div><div class="dr-skel-line s"></div></div>`;
    container.appendChild(s);
  }
}

/* Platform Switch */
async function dracinSwitchPlatform(btn) {
  // If switching to dramanova, ensure PIN auth
  const plat = btn.dataset.platform;
  if (plat === 'dramanova') {
    const token = sessionStorage.getItem('dramanova_token');
    if (!token) {
      // show modal
      document.getElementById('pinErr').style.display = 'none';
      document.getElementById('pinInput').value = '';
      document.getElementById('pinModal').style.display = 'flex';
      // wait for user action
      const ok = await new Promise(resolve => {
        document.getElementById('pinCancel').onclick = () => { document.getElementById('pinModal').style.display='none'; resolve(false); };
        document.getElementById('pinSubmit').onclick = async () => {
          const pin = document.getElementById('pinInput').value || '';
          try {
            const res = await fetch('/api/dracin/auth', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({pin}) });
            const j = await res.json();
            if (j.status === 'success' && j.token) {
              sessionStorage.setItem('dramanova_token', j.token);
              document.getElementById('pinModal').style.display='none';
              resolve(true);
            } else {
              document.getElementById('pinErr').textContent = j.message || 'PIN salah';
              document.getElementById('pinErr').style.display = 'block';
            }
          } catch (e) {
            document.getElementById('pinErr').textContent = 'Gagal memvalidasi PIN';
            document.getElementById('pinErr').style.display = 'block';
          }
        };
      });
      if (!ok) return; // abort if user cancelled or failed
    }
  }

  document.querySelectorAll('.dr-ptab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  DR.platform = plat;
  DR.browseMode = 'home';
  DR.page = 1;
  DR.seenBookKeys.clear();
  DR.fetchCache.clear();
  DR.inflight.clear();
  if (DR.browseController) DR.browseController.abort();
  document.getElementById('dracinSearchInput').value = '';
  document.getElementById('dracinBrowseTitle').textContent = 'SEMUA DRAMA';
  dracinLoadRank();
  dracinLoadBrowse(true);
}

// Helper fetch that attaches dramanova token when available
async function _dramanovaFetch(url, opts = {}) {
  await requireSubscriptionToken();
  const token = sessionStorage.getItem('dramanova_token');
  opts = opts || {};
  opts.headers = opts.headers || {};
  if (token) {
    opts.headers['X-Dramanova-Token'] = token;
    try {
      // fallback: append token as query param for servers that strip custom headers
      const u = new URL(url, location.origin);
      if (!u.searchParams.get('dramanova_token')) u.searchParams.set('dramanova_token', token);
      url = u.pathname + u.search + u.hash;
    } catch (e) {
      // ignore if URL parsing fails
    }
  }
  return appFetch(url, opts);
}

async function dracinFetchPayload(url, opts = {}) {
  const subToken = await requireSubscriptionToken();
  const token = `${subToken}|${sessionStorage.getItem('dramanova_token') || ''}`;
  const ttl = opts.ttl || 45000;
  const key = `${token}|${url}`;
  const cached = DR.fetchCache.get(key);
  if (cached && cached.expires > Date.now()) return cached.data;
  if (DR.inflight.has(key)) return DR.inflight.get(key);

  const reqOpts = {};
  if (opts.signal) reqOpts.signal = opts.signal;
  const promise = _dramanovaFetch(url, reqOpts)
    .then(res => res.json())
    .then(j => {
      const payload = (j?.status === "success") ? j.data : j;
      DR.fetchCache.set(key, { data: payload, expires: Date.now() + ttl });
      return payload;
    })
    .finally(() => DR.inflight.delete(key));

  DR.inflight.set(key, promise);
  return promise;
}

/* Search */
function dracinSearch() {
  const q = document.getElementById('dracinSearchInput').value.trim();
  if (!q) {
    DR.browseMode = 'home';
    DR.searchQuery = '';
    document.getElementById('dracinBrowseTitle').textContent = 'SEMUA DRAMA';
  } else {
    DR.browseMode = 'search';
    DR.searchQuery = q;
    document.getElementById('dracinBrowseTitle').textContent = `HASIL: "${esc(q)}"`;
  }
  DR.page = 1;
  DR.seenBookKeys.clear();
  dracinLoadBrowse(true);
}

function dracinLoadMore() {
  if (DR.loading || !DR.hasMore) return;
  DR.page++;
  dracinLoadBrowse(false);
}

/* Rank */
async function dracinLoadRank() {
  const seq = ++DR.rankSeq;
  const row = document.getElementById('dracinRankRow');
  const badge = document.getElementById('dracinRankBadge');
  dracinSkeletons(row, 8, true);
  badge.textContent = '';

  try {
    const payload = await dracinFetchPayload(`/api/dracin/rank?platform=${DR.platform}&lang=in`, { ttl: 120000 });
    if (seq !== DR.rankSeq) return;
    const books = payload?.books || [];

    row.innerHTML = '';
    badge.textContent = `${books.length} drama`;

    books.slice(0, 15).forEach((book, i) => {
      row.appendChild(dracinMakeCard(book, i + 1, true));
    });
  } catch (e) {
    if (e.name === 'AbortError') return;
    console.error(e);
    row.innerHTML = `<div style="padding:30px;color:#888;text-align:center">Gagal memuat trending</div>`;
  }
}

/* Browse / Home */
async function dracinLoadBrowse(reset = true) {
  if (DR.loading && !reset) return;
  if (reset && DR.browseController) {
    DR.browseController.abort();
    DR.inflight.clear();
  }
  const controller = new AbortController();
  DR.browseController = controller;
  const seq = ++DR.browseSeq;
  DR.loading = true;

  const grid = document.getElementById('dracin-grid');
  const loadBtn = document.getElementById('dracinLoadMore');

  if (reset) {
    dracinSkeletons(grid, 12);
    loadBtn.style.display = 'none';
  }

  dracinStatus(true, DR.browseMode === 'search' ? `Mencari...` : 'Memuat...');

  try {
    const url = DR.browseMode === 'search' 
      ? `/api/dracin/search?platform=${DR.platform}&keyword=${encodeURIComponent(DR.searchQuery)}&page=${DR.page}&lang=in`
      : `/api/dracin/home?platform=${DR.platform}&page=${DR.page}&size=${DR.pageSize}&lang=in`;

    const payload = await dracinFetchPayload(url, {
      signal: controller.signal,
      ttl: DR.browseMode === 'search' ? 45000 : 90000,
    });
    if (seq !== DR.browseSeq) return;

    if (reset) {
      grid.innerHTML = '';
      DR.seenBookKeys.clear();
    }

    const rawBooks = payload?.books || [];
    const books = dracinDedupeBooks(rawBooks, DR.seenBookKeys);
    DR.hasMore = payload?.hasMore === true || rawBooks.length >= DR.pageSize;
    if (!reset && rawBooks.length > 0 && books.length === 0) DR.hasMore = false;

    if (books.length === 0 && reset) {
      grid.innerHTML = `<div class="dr-empty"><h3>TIDAK ADA DATA</h3><p>Coba ganti platform atau kata kunci</p></div>`;
    } else {
      books.forEach(book => grid.appendChild(dracinMakeCard(book)));
    }

    loadBtn.style.display = DR.hasMore ? 'block' : 'none';
    dracinStatus(false);
  } catch (e) {
    if (e.name === 'AbortError') return;
    console.error(e);
    dracinStatus(false);
    if (reset) grid.innerHTML = `<div class="dr-empty"><h3>GAGAL</h3><p>${esc(e.message)}</p></div>`;
  } finally {
    if (seq === DR.browseSeq) {
      DR.loading = false;
      if (DR.browseController === controller) DR.browseController = null;
    }
  }
}
switchMainTab(_initialMainTab);
enterBrowseOrientationMode();
document.addEventListener("visibilitychange", () => {
  const playerOpen = document.getElementById("playerMode")?.style.display === "block";
  if (!document.hidden && !playerOpen) enterBrowseOrientationMode();
});
initSubscriptionGate();
</script>


/* ── PWA ── */
// Daftar domain CDN yang harus bypass service worker (fetch langsung)
// ByteDance / TikTok CDN untuk DramaBox, Melolo, ReelShort, dll.
const SW_BYPASS_HOSTS = [
  "fizzopic.org",
  "p16-novel-sign-sg.fizzopic.org",
  "p19-novel-sign-sg.fizzopic.org",
  "lf16-ttcdn-sg.fizzopic.org",
  "tiktokcdn.com",
  "muscdn.com",
  "byteimg.com",
  "bytedance.com",
  "static-aka.cubetv.cc",
  "video-aka.cubetv.cc",
];

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").then(reg => {
    // Kirim daftar bypass ke SW agar bisa dikecualikan di fetch handler
    if (reg.active) {
      reg.active.postMessage({ type: "SET_BYPASS_HOSTS", hosts: SW_BYPASS_HOSTS });
    }
    reg.addEventListener("updatefound", () => {
      const sw = reg.installing;
      if (sw) sw.addEventListener("statechange", () => {
        if (sw.state === "activated") {
          sw.postMessage({ type: "SET_BYPASS_HOSTS", hosts: SW_BYPASS_HOSTS });
        }
      });
    });
  }).catch(()=>{});

  // Intercept fetch dari SW — jika URL adalah CDN yang di-bypass, jangan biarkan SW handle
  navigator.serviceWorker.addEventListener("message", () => {});
}
let deferredPrompt;
const btnInstall = document.getElementById("btnInstallPwa");
window.addEventListener("beforeinstallprompt", e => {
  e.preventDefault(); deferredPrompt=e; btnInstall.style.display="block";
});
btnInstall.addEventListener("click", async () => {
  if (!deferredPrompt) return;
  deferredPrompt.prompt();
  const {outcome} = await deferredPrompt.userChoice;
  if (outcome==="accepted") btnInstall.style.display="none";
  deferredPrompt=null;
});
window.addEventListener("appinstalled", () => { btnInstall.style.display="none"; });
</script>
