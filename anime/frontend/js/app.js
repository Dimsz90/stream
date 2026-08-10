// API Configuration
const API_BASE = 'http://localhost:5000/api';
let currentAnimeUrl = '';
let currentEpisodes = [];
let currentEpisodeIndex = 0;

// ==================== Navigation ====================
function setupNavigation() {
    const allNavItems = document.querySelectorAll('.nav-item, .mobile-nav-item');
    allNavItems.forEach(item => {
        item.addEventListener('click', () => {
            const page = item.dataset.page;
            if (!page) return;

            // Sync active state across desktop and mobile navs
            document.querySelectorAll('.nav-item, .mobile-nav-item').forEach(nav => {
                if (nav.dataset.page === page) {
                    nav.classList.add('active');
                } else {
                    nav.classList.remove('active');
                }
            });

            showPage(page);
            loadPageData(page);
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });
    });
}
setupNavigation();

function showPage(pageName) {
    document.querySelectorAll('.page').forEach(page => page.classList.remove('active'));
    const targetPage = document.getElementById(`${pageName}-page`);
    if (targetPage) {
        targetPage.classList.add('active');
    }
}

// ==================== Home Page ====================
async function loadHomePage() {
    try {
        const response = await fetch(`${API_BASE}/home`);
        const data = await response.json();
        
        if (data.status === 'success') {
            displayAnimeGrid('latestAnime', data.data.latest);
            loadContinueWatching();
        }
    } catch (error) {
        console.error('Error loading homepage:', error);
    }
}

// ==================== Search ====================
document.getElementById('searchBtn').addEventListener('click', searchAnime);
document.getElementById('searchInput').addEventListener('keypress', (e) => {
    if (e.key === 'Enter') searchAnime();
});

async function searchAnime() {
    const query = document.getElementById('searchInput').value.trim();
    if (!query) return;
    
    try {
        const response = await fetch(`${API_BASE}/search?q=${query}`);
        const data = await response.json();
        
        if (data.status === 'success') {
            displayAnimeGrid('searchResults', data.data);
            showPage('search');
        }
    } catch (error) {
        console.error('Error searching:', error);
    }
}

// ==================== Anime Detail ====================
async function loadAnimeDetail(url) {
    currentAnimeUrl = url;
    
    try {
        const response = await fetch(`${API_BASE}/anime/detail`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });
        
        const data = await response.json();
        
        if (data.status === 'success') {
            displayAnimeDetail(data.data);
            currentEpisodes = data.data.episodes;
            showPage('detail');
        }
    } catch (error) {
        console.error('Error loading detail:', error);
    }
}

function displayAnimeDetail(detail) {
    const html = `
        <img src="${detail.thumbnail}" alt="${detail.title}">
        <div class="detail-info">
            <h1>${detail.title}</h1>
            <div class="meta">
                <span>📺 ${detail.total_episodes} Episodes</span>
                <span>📊 ${detail.status}</span>
                <span>🎭 ${detail.genre}</span>
            </div>
            <p class="synopsis">${detail.synopsis || 'No synopsis available.'}</p>
            <div class="progress-bar">
                <div class="progress" style="width: ${(detail.watched_episodes / parseInt(detail.total_episodes) * 100) || 0}%"></div>
            </div>
            <p>Watched: ${detail.watched_episodes}/${detail.total_episodes}</p>
        </div>
    `;
    
    document.getElementById('animeDetail').innerHTML = html;
    
    // Display episodes
    const epHtml = detail.episodes.map((ep, index) => `
        <div class="episode-card ${ep.watched ? 'watched' : ''}" 
             onclick="playEpisode('${ep.link}', ${index})">
            Ep ${ep.number}
        </div>
    `).join('');
    
    document.getElementById('episodeList').innerHTML = epHtml;
}

// ==================== Player ====================
async function playEpisode(url, index) {
    currentEpisodeIndex = index;
    
    try {
        // Get stream URL
        const response = await fetch(`${API_BASE}/stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });
        
        const data = await response.json();
        
        if (data.status === 'success' && data.data.stream_url) {
            // Open player modal
            document.getElementById('videoPlayer').src = data.data.stream_url;
            document.getElementById('playerModal').classList.add('active');
            
            // Update info
            document.getElementById('currentEpisode').textContent = 
                `Episode ${currentEpisodes[index]?.number || 'Unknown'}`;

            // Render Server / Quality selector
            const serverElem = document.getElementById('serverSelector');
            if (serverElem) {
                if (data.data.servers && data.data.servers.length > 0) {
                    const serverBtns = data.data.servers.map(srv => `
                        <button class="server-btn ${srv.url === data.data.stream_url ? 'active' : ''}" 
                                onclick="changeServer('${srv.url}', this)">
                            ${srv.name}
                        </button>
                    `).join('');
                    serverElem.innerHTML = `<div class="server-list"><label>Server / Quality:</label>${serverBtns}</div>`;
                } else {
                    serverElem.innerHTML = '';
                }
            }
            
            // Display download links
            if (data.data.downloads && data.data.downloads.length > 0) {
                const downloadHtml = data.data.downloads.map(dl => `
                    <a href="${dl.url}" target="_blank" class="download-link">
                        ${dl.quality} - ${dl.provider}
                    </a>
                `).join('');
                
                document.getElementById('downloadLinks').innerHTML = `
                    <h3>Download Links</h3>
                    <div class="download-links">${downloadHtml}</div>
                `;
            } else {
                document.getElementById('downloadLinks').innerHTML = '';
            }
            
            // Add to history
            addToHistory(url);
        } else {
            alert('Stream not available');
        }
    } catch (error) {
        console.error('Error loading stream:', error);
        alert('Failed to load stream');
    }
}

function changeServer(url, btnElem) {
    document.getElementById('videoPlayer').src = url;
    document.querySelectorAll('.server-btn').forEach(btn => btn.classList.remove('active'));
    if (btnElem) btnElem.classList.add('active');
}

// ==================== Player Controls ====================
document.querySelector('.close-modal').addEventListener('click', () => {
    document.getElementById('playerModal').classList.remove('active');
    document.getElementById('videoPlayer').src = '';
});

document.getElementById('prevEpisode').addEventListener('click', () => {
    if (currentEpisodeIndex > 0) {
        playEpisode(currentEpisodes[currentEpisodeIndex - 1].link, currentEpisodeIndex - 1);
    }
});

document.getElementById('nextEpisode').addEventListener('click', () => {
    if (currentEpisodeIndex < currentEpisodes.length - 1) {
        playEpisode(currentEpisodes[currentEpisodeIndex + 1].link, currentEpisodeIndex + 1);
    }
});

document.getElementById('bookmarkBtn').addEventListener('click', async () => {
    try {
        await fetch(`${API_BASE}/bookmark`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                anime_url: currentAnimeUrl,
                episode_url: currentEpisodes[currentEpisodeIndex]?.link,
                action: 'add'
            })
        });
        
        alert('Bookmarked!');
    } catch (error) {
        console.error('Error bookmarking:', error);
    }
});

// ==================== History ====================
async function addToHistory(episodeUrl) {
    try {
        await fetch(`${API_BASE}/history`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                anime_url: currentAnimeUrl,
                episode_url: episodeUrl,
                progress: '0'
            })
        });
    } catch (error) {
        console.error('Error adding history:', error);
    }
}

async function loadHistory() {
    try {
        const response = await fetch(`${API_BASE}/history`);
        const data = await response.json();
        
        if (data.status === 'success') {
            const html = data.data.map(item => `
                <div class="history-item" onclick="loadAnimeDetail('${item.anime_url}')">
                    <img src="${item.thumbnail}" alt="${item.anime_title}">
                    <div>
                        <h3>${item.anime_title}</h3>
                        <p>Episode ${item.episode_number}</p>
                        <p>Watched: ${new Date(item.watched_at).toLocaleString()}</p>
                    </div>
                </div>
            `).join('');
            
            document.getElementById('historyList').innerHTML = html || '<p>No history yet</p>';
        }
    } catch (error) {
        console.error('Error loading history:', error);
    }
}

// ==================== Bookmarks ====================
async function loadBookmarks() {
    try {
        const response = await fetch(`${API_BASE}/bookmarks`);
        const data = await response.json();
        
        if (data.status === 'success') {
            displayAnimeGrid('bookmarksList', data.data);
        }
    } catch (error) {
        console.error('Error loading bookmarks:', error);
    }
}

// ==================== Continue Watching ====================
async function loadContinueWatching() {
    try {
        const response = await fetch(`${API_BASE}/continue-watching`);
        const data = await response.json();
        
        if (data.status === 'success') {
            const container = document.getElementById('continueWatching') || 
                            document.getElementById('continueList');
            if (container) {
                const html = data.data.map(item => `
                    <div class="anime-card" onclick="${item.next_episode_url ? 
                        `playEpisode('${item.next_episode_url}', 0)` : 
                        `loadAnimeDetail('${item.anime_url}')`}">
                        <img src="${item.thumbnail}" alt="${item.anime_title}">
                        <div class="card-info">
                            <h3>${item.anime_title}</h3>
                            <p>Last: Ep ${item.last_watched}</p>
                            <p>Progress: ${item.progress_percentage}%</p>
                        </div>
                    </div>
                `).join('');
                
                container.innerHTML = html || '<p>Start watching some anime!</p>';
            }
        }
    } catch (error) {
        console.error('Error loading continue watching:', error);
    }
}

// ==================== Statistics ====================
async function loadStats() {
    try {
        const response = await fetch(`${API_BASE}/stats`);
        const data = await response.json();
        
        if (data.status === 'success') {
            const html = `
                <div class="stat-card">
                    <h3>${data.data.total_anime_in_db}</h3>
                    <p>Anime in Database</p>
                </div>
                <div class="stat-card">
                    <h3>${data.data.total_episodes_watched}</h3>
                    <p>Episodes Watched</p>
                </div>
                <div class="stat-card">
                    <h3>${data.data.estimated_hours_watched}</h3>
                    <p>Hours Watched</p>
                </div>
                <div class="stat-card">
                    <h3>${data.data.total_bookmarks}</h3>
                    <p>Bookmarks</p>
                </div>
            `;
            
            document.getElementById('statsData').innerHTML = html;
        }
    } catch (error) {
        console.error('Error loading stats:', error);
    }
}

// ==================== Random Anime ====================
document.getElementById('randomBtn').addEventListener('click', async () => {
    try {
        const response = await fetch(`${API_BASE}/anime/random`);
        const data = await response.json();
        
        if (data.status === 'success') {
            loadAnimeDetail(data.data.url);
        }
    } catch (error) {
        console.error('Error getting random anime:', error);
    }
});

// ==================== Export Bookmarks ====================
document.getElementById('exportBookmarks')?.addEventListener('click', async () => {
    try {
        const response = await fetch(`${API_BASE}/export/bookmarks`);
        const data = await response.json();
        
        if (data.status === 'success') {
            const blob = new Blob([JSON.stringify(data.data, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'bookmarks.json';
            a.click();
            URL.revokeObjectURL(url);
        }
    } catch (error) {
        console.error('Error exporting bookmarks:', error);
    }
});

// ==================== Display Helpers ====================
function displayAnimeGrid(containerId, items) {
    const container = document.getElementById(containerId);
    if (!container) return;
    
    if (!items || items.length === 0) {
        container.innerHTML = '<p>No results found</p>';
        return;
    }
    
    const html = items.map(item => `
        <div class="anime-card" onclick="loadAnimeDetail('${item.link || item.anime_url}')">
            ${item.episode || item.episodes ? 
                `<span class="episode-badge">${item.episode || item.episodes}</span>` : ''}
            <img src="${item.thumbnail}" alt="${item.title || item.anime_title}">
            <div class="card-info">
                <h3>${item.title || item.anime_title}</h3>
                ${item.genres ? `<p>${item.genres.join(', ')}</p>` : ''}
            </div>
        </div>
    `).join('');
    
    container.innerHTML = html;
}

function loadPageData(page) {
    switch(page) {
        case 'home':
            loadHomePage();
            break;
        case 'bookmarks':
            loadBookmarks();
            break;
        case 'history':
            loadHistory();
            break;
        case 'continue':
            loadContinueWatching();
            break;
        case 'stats':
            loadStats();
            break;
    }
}

// Back button
document.addEventListener('click', (e) => {
    if (e.target.classList.contains('back-btn')) {
        showPage('home');
        loadHomePage();
    }
});

// Initial load
loadHomePage();