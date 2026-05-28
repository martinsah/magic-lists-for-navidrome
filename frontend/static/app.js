// Global state for artist selection
let selectedArtistId = null;
let selectedGenre = null;
let selectedMetaGenre = null;
let genreSelectionMode = 'raw';
let allArtists = [];
let allGenres = [];
let allMetaGenres = [];
let currentToast = null;
let latestMetaInsights = null;

// Global state for library selection
let selectedLibraryIds = [];
let allLibraries = [];

// Lidarr integration (loaded when viewing Manage Playlists)
let lidarrStatus = { enabled: false, configured: false, reachable: false };
let pendingLidarrPick = null;
let pendingBulkLidarrPlaylistId = null;
const expandedPlaylistIds = new Set();


// Format dates as YYYY-MM-DD (local calendar date)
function formatDisplayDate(dateString) {
    if (!dateString) return 'Never';

    const date = new Date(dateString);
    if (Number.isNaN(date.getTime())) return 'Never';

    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

function formatFriendlyDate(dateString) {
    return formatDisplayDate(dateString);
}

// Toast utility functions
function showToast(type, message, duration = 5000) {
    // Remove any existing toast
    if (currentToast) {
        hideToast(currentToast);
    }

    const container = document.getElementById('toast-container');
    const toastId = 'toast-' + Date.now();

    let bgClass, textClass, borderClass, icon;

    if (type === 'success') {
        bgClass = 'bg-green-50 border-green-200';
        textClass = 'text-green-800';
        borderClass = 'border';
        icon = '<svg class="size-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>';
    } else if (type === 'loading') {
        bgClass = 'bg-blue-50 border-blue-200';
        textClass = 'text-blue-800';
        borderClass = 'border';
        icon = '<svg class="size-4 animate-spin" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>';
    } else {
        bgClass = 'bg-red-50 border-red-200';
        textClass = 'text-red-800';
        borderClass = 'border';
        icon = '<svg class="size-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>';
    }

    const toast = document.createElement('div');
    toast.id = toastId;
    toast.className = `${bgClass} ${borderClass} ${textClass} rounded-lg shadow-lg p-4 pointer-events-auto transition-all duration-300 transform translate-x-0 opacity-100`;
    toast.innerHTML = `
        <div class="flex items-center gap-3">
            <div class="flex-shrink-0">
                ${icon}
            </div>
            <div class="flex-grow">
                <p class="text-sm font-medium">${message}</p>
            </div>
            ${type !== 'loading' ? `
            <button type="button" class="flex-shrink-0 inline-flex items-center justify-center size-5 rounded-lg text-gray-800 hover:bg-gray-200 focus:outline-none focus:ring-2 focus:ring-gray-400" onclick="hideToast('${toastId}')">
                <span class="sr-only">Close</span>
                <svg class="size-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                </svg>
            </button>
            ` : ''}
        </div>
    `;

    container.appendChild(toast);
    currentToast = toastId;

    // Auto-dismiss (except for loading toasts)
    if (type !== 'loading' && duration > 0) {
        setTimeout(() => hideToast(toastId), duration);
    }

    return toastId;
}

function hideToast(toastId) {
    const toast = document.getElementById(toastId);
    if (toast) {
        toast.classList.add('translate-x-full', 'opacity-0');
        setTimeout(() => {
            if (toast.parentNode) {
                toast.parentNode.removeChild(toast);
            }
            if (currentToast === toastId) {
                currentToast = null;
            }
        }, 300);
    }
}

// Mobile menu toggle functionality
const mobileMenuBtn = document.getElementById('hs-navbar-alignment-collapse');
const mobileSidebar = document.getElementById('mobileSidebar');
const sidebarOverlay = document.getElementById('sidebarOverlay');
const closeMobileSidebarBtn = document.getElementById('closeMobileSidebar');

mobileMenuBtn.addEventListener('click', function() {
    mobileSidebar.classList.toggle('-translate-x-full');
    sidebarOverlay.classList.toggle('hidden');
});

// Close sidebar when clicking on close button
closeMobileSidebarBtn.addEventListener('click', function() {
    mobileSidebar.classList.add('-translate-x-full');
    sidebarOverlay.classList.add('hidden');
});

// Close sidebar when clicking on overlay
sidebarOverlay.addEventListener('click', function() {
    mobileSidebar.classList.add('-translate-x-full');
    sidebarOverlay.classList.add('hidden');
});

// Close sidebar when clicking outside on mobile
document.addEventListener('click', function(event) {
    if (window.innerWidth < 768 && 
        !mobileSidebar.contains(event.target) && 
        !mobileMenuBtn.contains(event.target) &&
        !mobileSidebar.classList.contains('-translate-x-full')) {
        mobileSidebar.classList.add('-translate-x-full');
        sidebarOverlay.classList.add('hidden');
    }
});

// Handle window resize to ensure proper state
window.addEventListener('resize', function() {
    if (window.innerWidth >= 768) {
        mobileSidebar.classList.add('-translate-x-full'); // Hide mobile sidebar on large screens
        sidebarOverlay.classList.add('hidden'); // Hide overlay on large screens
    } else {
        // On mobile, ensure sidebar is hidden when switching from desktop view
        mobileSidebar.classList.add('-translate-x-full');
        sidebarOverlay.classList.add('hidden');
    }
});

// Sidebar navigation active state management
function setActiveMenuItem(page) {
    // Remove active state from all links in desktop sidebar
    const desktopLinks = document.querySelectorAll('#desktopSidebar [data-page]');
    desktopLinks.forEach(link => {
        link.classList.remove('bg-gray-200');
        link.classList.add('bg-gray-100');
    });
    
    // Remove active state from all links in mobile sidebar
    const mobileLinks = document.querySelectorAll('#mobileSidebar [data-page]');
    mobileLinks.forEach(link => {
        link.classList.remove('bg-gray-200');
        link.classList.add('bg-white');
    });
    
    // Add active state to clicked desktop sidebar links
    const activeDesktopLinks = document.querySelectorAll(`#desktopSidebar [data-page="${page}"]`);
    activeDesktopLinks.forEach(link => {
        link.classList.add('bg-gray-200');
        link.classList.remove('bg-gray-100');
    });
    
    // Add active state to clicked mobile sidebar links
    const activeMobileLinks = document.querySelectorAll(`#mobileSidebar [data-page="${page}"]`);
    activeMobileLinks.forEach(link => {
        link.classList.add('bg-gray-200');
        link.classList.remove('bg-white');
    });
}

// Navigation functionality
function showContent(contentId) {
    // Hide all content sections
    const contentSections = ['welcome-content', 'this-is-content', 'rediscover-content', 'genre-mix-content', 'manage-playlists-content', 'genre-insights-content', 'system-check-content'];
    contentSections.forEach(id => {
        const element = document.getElementById(id);
        if (element) {
            element.style.display = 'none';
        }
    });

    // Show the selected content
    const targetContent = document.getElementById(contentId);
    if (targetContent) {
        targetContent.style.display = 'block';
    }
}

// Add click handlers to all navigation links
document.addEventListener('click', function(event) {
    const link = event.target.closest('[data-page]');
    if (link) {
        event.preventDefault();
        const page = link.getAttribute('data-page');
        
        // Use the shared navigation handler
        handlePageNavigation(page);
        
        // Update URL based on page (only for click navigation, not popstate)
        updateURL(page);
        
        // Close mobile sidebar if clicked
        if (window.innerWidth < 768) {
            mobileSidebar.classList.add('-translate-x-full');
            sidebarOverlay.classList.add('hidden');
        }
    }
});

// AI model information cache
let aiModelInfo = null;

// Get AI model information for analytics
async function getAIModelInfo() {
    if (aiModelInfo) {
        return aiModelInfo;
    }
    
    try {
        const response = await fetch('/api/ai-model-info');
        if (response.ok) {
            aiModelInfo = await response.json();
            return aiModelInfo;
        }
    } catch (error) {
        console.error('Error fetching AI model info:', error);
    }
    
    // Fallback
    return {
        provider: 'unknown',
        model: 'unknown',
        has_api_key: false
    };
}

// Post-launch library size tracking
async function trackLibrarySize() {
    try {
        const response = await fetch('/api/track-library-size', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });

        if (response.ok) {
            const data = await response.json();

            if (data.tracked && typeof window.rybbit !== 'undefined') {
                // Track library size event with Rybbit
                window.rybbit.event('Library Size Tracked', {
                    songCount: data.song_count,
                    userId: data.user_id
                });
                console.log('📊 Library size tracked for analytics');
            }
        }
    } catch (error) {
        console.error('❌ Error tracking library size:', error);
    }
}

async function checkDatabaseConnectivity() {
    const alertDiv = document.getElementById('database-error-alert');

    try {
        const response = await fetch('/api/playlists');
        if (response.ok) {
            // Database is accessible, hide alert
            alertDiv.classList.add('hidden');
        } else {
            // Database error, show alert
            alertDiv.classList.remove('hidden');
        }
    } catch (error) {
        // Network/database error, show alert
        alertDiv.classList.remove('hidden');
        console.error('Database connectivity check failed:', error);
    }
}

// Initialize on DOM load
document.addEventListener('DOMContentLoaded', function() {
    // Initialize Preline components
    if (window.HSStaticMethods) {
        window.HSStaticMethods.autoInit();
        console.log('Preline initialized');
    } else {
        console.error('Preline not loaded');
    }
    
    // Setup artist selection change handler
    const artistSelect = document.getElementById('artist-search-select');
    if (artistSelect) {
        artistSelect.addEventListener('change', handleArtistSelection);
        // Add validation on click - highlight library selector if no libraries selected
        artistSelect.addEventListener('click', function() {
            if (selectedLibraryIds.length === 0) {
                // Apply validation styling to library selectors
                const libraryMulti = document.getElementById('library-multi');
                const mobileLibraryMulti = document.getElementById('mobile-library-multi');
                if (libraryMulti) {
                    libraryMulti.classList.add('ring-2', 'ring-red-500', 'ring-opacity-50');
                    setTimeout(() => libraryMulti.classList.remove('ring-2', 'ring-red-500', 'ring-opacity-50'), 3000);
                }
                if (mobileLibraryMulti) {
                    mobileLibraryMulti.classList.add('ring-2', 'ring-red-500', 'ring-opacity-50');
                    setTimeout(() => mobileLibraryMulti.classList.remove('ring-2', 'ring-red-500', 'ring-opacity-50'), 3000);
                }
                showToast('warning', 'Please select a music library first.');
            }
        });
    }
    
    // Reload genre list when minimum-track filter changes
    document.querySelectorAll('input[name="genre-min-song-count"]').forEach(radio => {
        radio.addEventListener('change', () => {
            if (selectedLibraryIds.length > 0) {
                loadGenres();
                loadMetaGenres();
            }
        });
    });

    document.querySelectorAll('input[name="genre-selection-mode"]').forEach(radio => {
        radio.addEventListener('change', handleGenreSelectionModeChange);
    });
    handleGenreSelectionModeChange();

    const refreshMetaBtn = document.getElementById('refresh-meta-genres-btn');
    if (refreshMetaBtn) {
        refreshMetaBtn.addEventListener('click', refreshMetaGenresNow);
    }
    const saveMetaSettingsBtn = document.getElementById('save-meta-settings-btn');
    if (saveMetaSettingsBtn) {
        saveMetaSettingsBtn.addEventListener('click', saveMetaGenreSettings);
    }

    // Load libraries on page load
    loadLibraries();

    // Load playlist count on page load
    updatePlaylistCount();

    // Handle initial page routing (with small delay to ensure DOM is ready)
    setTimeout(() => {
        const currentPage = getPageFromURL(window.location.pathname);
        handlePageNavigation(currentPage);
    }, 100);

    // Track library size post-launch (with delay to not interfere with app loading)
    setTimeout(trackLibrarySize, 2000);

    // Check database connectivity
    checkDatabaseConnectivity();
});

// Load artists and populate the select (from original working code)
async function loadArtists() {
    try {
        let url = '/api/artists';
        if (selectedLibraryIds.length > 0) {
            const libraryIdsParam = selectedLibraryIds.map(id => `library_id=${encodeURIComponent(id)}`).join('&');
            url = `/api/artists?${libraryIdsParam}`;
        }
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error('Failed to fetch artists');
        }
        allArtists = await response.json();

        // Clear any previous selection
        selectedArtistId = null;

        // Populate the select dropdown
        const artistSelect = document.getElementById('artist-search-select');
        if (artistSelect) {
            // Clear existing options except the first one
            while (artistSelect.options.length > 1) {
                artistSelect.remove(1);
            }

            // Add artist options
            allArtists.forEach(artist => {
                const option = document.createElement('option');
                option.value = artist.id;
                option.textContent = artist.name;
                artistSelect.appendChild(option);
            });

            // Reinitialize the HSSelect component
            if (window.HSSelect) {
                const selectInstance = window.HSSelect.getInstance(artistSelect);
                if (selectInstance) {
                    selectInstance.destroy();
                }
                window.HSSelect.autoInit();
            }
        }
    } catch (error) {
        console.error('Error loading artists:', error);
        showToast('error', 'Failed to load artists from your library');
    }
}

function getGenreMinSongCount() {
    const selected = document.querySelector('input[name="genre-min-song-count"]:checked');
    return selected ? parseInt(selected.value, 10) : 25;
}

function getGenreSelectionMode() {
    const selected = document.querySelector('input[name="genre-selection-mode"]:checked');
    return selected ? selected.value : 'raw';
}

function updateGenreSubmitEnabled() {
    const submitBtn = document.getElementById('create-genre-playlist-btn');
    if (!submitBtn) return;
    if (genreSelectionMode === 'meta') {
        submitBtn.disabled = !selectedMetaGenre;
    } else {
        submitBtn.disabled = !selectedGenre;
    }
}

function handleGenreSelectionModeChange() {
    genreSelectionMode = getGenreSelectionMode();
    const rawWrap = document.getElementById('raw-genre-select-wrap');
    const metaWrap = document.getElementById('meta-genre-select-wrap');

    if (rawWrap) rawWrap.classList.toggle('hidden', genreSelectionMode !== 'raw');
    if (metaWrap) metaWrap.classList.toggle('hidden', genreSelectionMode !== 'meta');

    if (genreSelectionMode === 'meta' && selectedLibraryIds.length > 0) {
        loadMetaGenres();
    }
    updateGenreSubmitEnabled();
}

async function loadGenres() {
    try {
        const params = [`min_song_count=${getGenreMinSongCount()}`];
        if (selectedLibraryIds.length > 0) {
            selectedLibraryIds.forEach(id => {
                params.push(`library_id=${encodeURIComponent(id)}`);
            });
        }
        const url = `/api/genres?${params.join('&')}`;
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error('Failed to fetch genres');
        }
        allGenres = await response.json();

        // Get the genre select element
        const genreSelect = document.getElementById('genre-select');

        // Update the placeholder text to show genre count
        if (genreSelect && window.HSSelect) {
            const selectInstance = window.HSSelect.getInstance(genreSelect);
            if (selectInstance) {
                selectInstance.destroy();
            }

            // Update the data-hs-select attribute with new placeholder
            const newPlaceholder = `Select from ${allGenres.length} genres...`;
            genreSelect.setAttribute('data-hs-select', JSON.stringify({
                "placeholder": newPlaceholder,
                "toggleTag": "<button type=\"button\"></button>",
                "toggleClasses": "hs-select-disabled:pointer-events-none hs-select-disabled:opacity-50 relative py-3 px-4 pe-9 flex text-nowrap w-full cursor-pointer bg-white border border-gray-200 rounded-lg text-start text-sm focus:border-blue-500 focus:ring-blue-500 before:absolute before:inset-0 before:z-[1]",
                "dropdownClasses": "hs-select-dropdown mt-2 z-50 w-full max-h-72 p-1 space-y-0.5 bg-white border border-gray-200 rounded-lg overflow-hidden overflow-y-auto [&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-track]:bg-gray-100 [&::-webkit-scrollbar-thumb]:bg-gray-300",
                "optionClasses": "py-2 px-4 w-full text-sm text-gray-800 cursor-pointer hover:bg-gray-100 rounded-lg focus:outline-none focus:bg-gray-100",
                "optionTemplate": "<div class=\"flex justify-between items-center w-full\"><span data-title></span><span class=\"hidden hs-selected:block\"><svg class=\"flex-shrink-0 size-3.5 text-blue-600\" xmlns=\"http://www.w3.org/2000/svg\" width=\"24\" height=\"24\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><polyline points=\"20 6 9 17 4 12\"/></svg></span></div>",
                "hasSearch": true,
                "searchPlaceholder": "Search...",
                "searchClasses": "block w-full text-sm border-gray-200 rounded-lg focus:border-blue-500 focus:ring-blue-500 before:absolute before:inset-0 before:z-[1] py-2 px-3",
                "searchWrapperClasses": "bg-white p-2 -mx-1 sticky top-0"
            }));
        }

        // Clear any previous selection
        selectedGenre = null;
        updateGenreSubmitEnabled();

        // Populate the select dropdown
        if (genreSelect) {
            genreSelect.value = '';

            // Clear existing options except the first one
            while (genreSelect.options.length > 1) {
                genreSelect.remove(1);
            }

            // Add genre options
            allGenres.forEach(genre => {
                const option = document.createElement('option');
                option.value = genre.name;
                option.textContent = `${genre.name} (${genre.songCount})`;
                genreSelect.appendChild(option);
            });

            if (!genreSelect.dataset.changeListenerAttached) {
                genreSelect.addEventListener('change', handleGenreSelection);
                genreSelect.dataset.changeListenerAttached = 'true';
            }

            // Reinitialize the HSSelect component
            if (window.HSSelect) {
                window.HSSelect.autoInit();
            }
        }
    } catch (error) {
        console.error('Error loading genres:', error);
        showToast('error', 'Failed to load genres from your library');
    }
}

async function loadMetaGenres() {
    try {
        const params = [`min_song_count=${getGenreMinSongCount()}`];
        if (selectedLibraryIds.length > 0) {
            selectedLibraryIds.forEach(id => {
                params.push(`library_id=${encodeURIComponent(id)}`);
            });
        }
        const response = await fetch(`/api/genres/meta?${params.join('&')}`);
        if (!response.ok) {
            throw new Error('Failed to fetch meta genres');
        }
        const data = await response.json();
        allMetaGenres = data.groups || [];

        const metaSelect = document.getElementById('meta-genre-select');
        if (!metaSelect) return;

        selectedMetaGenre = null;
        metaSelect.value = '';
        while (metaSelect.options.length > 1) {
            metaSelect.remove(1);
        }
        allMetaGenres.forEach(group => {
            const option = document.createElement('option');
            option.value = group.meta_genre;
            option.textContent = `${group.meta_genre} (${group.genres.length} genres, ${group.total_song_count} tracks)`;
            metaSelect.appendChild(option);
        });

        if (!metaSelect.dataset.changeListenerAttached) {
            metaSelect.addEventListener('change', handleMetaGenreSelection);
            metaSelect.dataset.changeListenerAttached = 'true';
        }
        updateGenreSubmitEnabled();
    } catch (error) {
        console.error('Error loading meta genres:', error);
        showToast('error', 'Failed to load distilled meta-genres');
    }
}

async function loadGenreInsights() {
    try {
        const params = [];
        if (selectedLibraryIds.length > 0) {
            selectedLibraryIds.forEach(id => params.push(`library_id=${encodeURIComponent(id)}`));
        }
        const query = params.length > 0 ? `?${params.join('&')}` : '';
        const response = await fetch(`/api/genres/meta/insights${query}`);
        if (!response.ok) {
            throw new Error('Failed to load genre insights');
        }
        latestMetaInsights = await response.json();
        renderGenreInsights(latestMetaInsights);
    } catch (error) {
        console.error('Error loading genre insights:', error);
        showToast('error', 'Failed to load genre insights');
    }
}

function renderGenreInsights(insights) {
    const cards = document.getElementById('genre-insights-cards');
    const groupsEl = document.getElementById('genre-insights-groups');
    const warningEl = document.getElementById('genre-insights-warning');
    if (!cards || !groupsEl || !warningEl) return;

    const settings = insights.settings || {};
    const values = [
        ['Last Generated', formatDisplayDate(insights.generated_at)],
        ['Last Refresh', formatDisplayDate(insights.last_refresh_at)],
        ['Next Refresh', formatDisplayDate(insights.next_refresh_at)],
        ['Model', insights.model_name || 'Fallback/None'],
        ['Raw Genres', String(insights.raw_genre_count || 0)],
        ['Meta Groups', String(insights.total_groups || 0)],
        ['Singleton Groups', String(insights.singleton_groups || 0)],
        ['Stale', insights.stale ? 'Yes' : 'No'],
    ];
    cards.innerHTML = values.map(([label, value]) => `
        <div class="border border-gray-200 rounded-lg p-3 bg-gray-50">
            <div class="text-xs uppercase tracking-wide text-gray-500 mb-1">${label}</div>
            <div class="text-sm font-semibold text-gray-900 break-all">${value}</div>
        </div>
    `).join('');

    document.getElementById('meta-refresh-frequency').value = settings.refresh_frequency || 'weekly';
    document.getElementById('meta-min-song-count').value = settings.min_song_count ?? 0;
    document.getElementById('meta-min-raw-genres').value = settings.min_raw_genres ?? 30;
    document.getElementById('meta-cache-hours').value = settings.cache_hours ?? 168;

    const singletonRatio = insights.singleton_ratio || 0;
    if (singletonRatio >= 0.7 && (insights.total_groups || 0) > 0) {
        warningEl.classList.remove('hidden');
        warningEl.textContent = `Warning: ${(singletonRatio * 100).toFixed(0)}% of groups are singletons. This can indicate fallback grouping or weak distillation output.`;
    } else {
        warningEl.classList.add('hidden');
        warningEl.textContent = '';
    }

    const groups = insights.groups || [];
    if (groups.length === 0) {
        groupsEl.innerHTML = '<div class="text-sm text-gray-500 p-3 bg-gray-50 rounded-lg">No distillation snapshot yet. Click \"Refresh Meta-Genres Now\" to create one.</div>';
        return;
    }
    groupsEl.innerHTML = groups.map(group => `
        <div class="border border-gray-200 rounded-lg p-3">
            <div class="flex items-center justify-between gap-3">
                <div class="font-medium text-gray-900">${group.meta_genre}</div>
                <div class="text-xs text-gray-500">${(group.genres || []).length} genres • ${group.total_song_count || 0} tracks</div>
            </div>
            <div class="text-xs text-gray-600 mt-2">${(group.genres || []).join(', ')}</div>
        </div>
    `).join('');
}

async function refreshMetaGenresNow() {
    const button = document.getElementById('refresh-meta-genres-btn');
    if (button) button.disabled = true;
    try {
        const configuredMinSongCount = parseInt(document.getElementById('meta-min-song-count')?.value || '0', 10);
        const params = [`min_song_count=${Number.isNaN(configuredMinSongCount) ? 0 : configuredMinSongCount}`];
        if (selectedLibraryIds.length > 0) {
            selectedLibraryIds.forEach(id => params.push(`library_id=${encodeURIComponent(id)}`));
        }
        const response = await fetch(`/api/genres/meta/refresh?${params.join('&')}`, { method: 'POST' });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Failed to refresh meta genres' }));
            throw new Error(errorData.detail || 'Failed to refresh meta genres');
        }
        showToast('success', 'Meta-genres refreshed');
        await loadMetaGenres();
        await loadGenreInsights();
    } catch (error) {
        console.error('Error refreshing meta-genres:', error);
        showToast('error', error.message);
    } finally {
        if (button) button.disabled = false;
    }
}

async function saveMetaGenreSettings() {
    const button = document.getElementById('save-meta-settings-btn');
    if (button) button.disabled = true;
    try {
        const payload = {
            refresh_frequency: document.getElementById('meta-refresh-frequency').value,
            min_song_count: parseInt(document.getElementById('meta-min-song-count').value || '0', 10),
            min_raw_genres: parseInt(document.getElementById('meta-min-raw-genres').value || '30', 10),
            cache_hours: parseInt(document.getElementById('meta-cache-hours').value || '168', 10),
        };
        const response = await fetch('/api/genres/meta/settings', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Failed to save settings' }));
            throw new Error(errorData.detail || 'Failed to save settings');
        }
        showToast('success', 'Distillation settings updated');
        await loadGenreInsights();
    } catch (error) {
        console.error('Error saving meta-genre settings:', error);
        showToast('error', error.message);
    } finally {
        if (button) button.disabled = false;
    }
}

// Handle genre selection change
function handleGenreSelection(e) {
    selectedGenre = e.target.value;
    updateGenreSubmitEnabled();
}

function handleMetaGenreSelection(e) {
    selectedMetaGenre = e.target.value;
    updateGenreSubmitEnabled();
}

// Load libraries and populate the multi-select interface
async function loadLibraries() {
    try {
        console.log('📚 Loading libraries from API...');
        console.log(`📚 Current localStorage:`, localStorage.getItem('selectedLibraryIds'));
        const response = await fetch('/api/music-folders');
        if (!response.ok) {
            throw new Error(`Failed to fetch libraries: ${response.status} ${response.statusText}`);
        }
        allLibraries = await response.json();
        console.log(`📚 Loaded ${allLibraries.length} libraries:`, allLibraries);

        // Clear any previous selection
        selectedLibraryIds = [];

        // Get UI elements
        const desktopLoading = document.getElementById('library-loading');
        const desktopSingle = document.getElementById('library-single');
        const desktopSingleName = document.getElementById('library-single-name');
        const desktopMulti = document.getElementById('library-multi');
        const desktopMultiText = document.getElementById('library-multi-text');
        const desktopCheckboxes = document.getElementById('library-checkboxes');

        const mobileLoading = document.getElementById('mobile-library-loading');
        const mobileSingle = document.getElementById('mobile-library-single');
        const mobileSingleName = document.getElementById('mobile-library-single-name');
        const mobileMulti = document.getElementById('mobile-library-multi');
        const mobileMultiText = document.getElementById('mobile-library-multi-text');
        const mobileCheckboxes = document.getElementById('mobile-library-checkboxes');

        // Hide loading states
        if (desktopLoading) desktopLoading.classList.add('hidden');
        if (mobileLoading) mobileLoading.classList.add('hidden');

        // Hide all states initially
        if (desktopSingle) desktopSingle.classList.add('hidden');
        if (mobileSingle) mobileSingle.classList.add('hidden');
        if (desktopMulti) desktopMulti.classList.add('hidden');
        if (mobileMulti) mobileMulti.classList.add('hidden');

        if (allLibraries.length === 1) {
            // Single library - show read-only display (AC1)
            const library = allLibraries[0];
            selectedLibraryIds = [library.id];

            if (desktopSingle && desktopSingleName) {
                desktopSingleName.textContent = library.name;
                desktopSingle.classList.remove('hidden');
            }
            if (mobileSingle && mobileSingleName) {
                mobileSingleName.textContent = library.name;
                mobileSingle.classList.remove('hidden');
            }

            console.log(`📚 Single library detected: ${library.name} (ID: ${library.id}) - showing readonly display`);

            // Save to localStorage
            localStorage.setItem('selectedLibraryIds', JSON.stringify(selectedLibraryIds));
            console.log(`📚 Saved to localStorage:`, selectedLibraryIds);

        } else {
            // Multiple libraries - show multi-select interface (AC2)
            console.log(`📚 Multiple libraries detected: ${allLibraries.length} libraries - showing multi-select`);

            // Load saved library selections from localStorage
            const savedLibraryIds = localStorage.getItem('selectedLibraryIds');
            if (savedLibraryIds) {
                try {
                    const parsedIds = JSON.parse(savedLibraryIds);
                    // Filter to only include libraries that still exist
                    selectedLibraryIds = parsedIds.filter(id => allLibraries.some(lib => lib.id === id));
                    console.log(`📚 Loaded saved library selections:`, selectedLibraryIds);
                } catch (e) {
                    console.warn('📚 Invalid saved library IDs, starting fresh');
                    selectedLibraryIds = [];
                }
            } else {
                console.log('📚 No saved library selections found');
                selectedLibraryIds = [];
            }

            // Create checkboxes for desktop
            if (desktopCheckboxes) {
                desktopCheckboxes.innerHTML = '';
                allLibraries.forEach(library => {
                    const checkboxDiv = document.createElement('div');
                    checkboxDiv.className = 'flex items-center px-3 py-2 hover:bg-gray-50 rounded';
                    checkboxDiv.innerHTML = `
                        <input type="checkbox"
                               id="desktop-lib-${library.id}"
                               value="${library.id}"
                               class="shrink-0 mt-0.5 border-gray-200 rounded text-blue-600 focus:ring-blue-500"
                               ${selectedLibraryIds.includes(library.id) ? 'checked' : ''}>
                        <label for="desktop-lib-${library.id}" class="ml-2 text-sm text-gray-800 cursor-pointer">
                            ${library.name}
                        </label>
                    `;
                    desktopCheckboxes.appendChild(checkboxDiv);
                });
            }

            // Create checkboxes for mobile
            if (mobileCheckboxes) {
                mobileCheckboxes.innerHTML = '';
                allLibraries.forEach(library => {
                    const checkboxDiv = document.createElement('div');
                    checkboxDiv.className = 'flex items-center px-3 py-2 hover:bg-gray-50 rounded';
                    checkboxDiv.innerHTML = `
                        <input type="checkbox"
                               id="mobile-lib-${library.id}"
                               value="${library.id}"
                               class="shrink-0 mt-0.5 border-gray-200 rounded text-blue-600 focus:ring-blue-500"
                               ${selectedLibraryIds.includes(library.id) ? 'checked' : ''}>
                        <label for="mobile-lib-${library.id}" class="ml-2 text-sm text-gray-800 cursor-pointer">
                            ${library.name}
                        </label>
                    `;
                    mobileCheckboxes.appendChild(checkboxDiv);
                });
            }

            // Update display text
            updateLibraryDisplayText();

            // Show multi-select interfaces
            if (desktopMulti) desktopMulti.classList.remove('hidden');
            if (mobileMulti) mobileMulti.classList.remove('hidden');

            // Add event listeners for dropdown toggles
            const desktopToggle = document.getElementById('library-multi-toggle');
            const desktopDropdown = document.getElementById('library-multi-dropdown');
            const mobileToggle = document.getElementById('mobile-library-multi-toggle');
            const mobileDropdown = document.getElementById('mobile-library-multi-dropdown');

            if (desktopToggle && desktopDropdown) {
                desktopToggle.addEventListener('click', (e) => {
                    e.stopPropagation();
                    desktopDropdown.classList.toggle('hidden');
                });
            }
            if (mobileToggle && mobileDropdown) {
                mobileToggle.addEventListener('click', (e) => {
                    e.stopPropagation();
                    mobileDropdown.classList.toggle('hidden');
                });
            }

            // Add event listeners for checkboxes
            allLibraries.forEach(library => {
                const desktopCheckbox = document.getElementById(`desktop-lib-${library.id}`);
                const mobileCheckbox = document.getElementById(`mobile-lib-${library.id}`);

                if (desktopCheckbox) {
                    desktopCheckbox.addEventListener('change', handleLibraryCheckboxChange);
                }
                if (mobileCheckbox) {
                    mobileCheckbox.addEventListener('change', handleLibraryCheckboxChange);
                }
            });

            // Close dropdowns when clicking outside
            document.addEventListener('click', (e) => {
                if (desktopDropdown && !desktopMulti.contains(e.target)) {
                    desktopDropdown.classList.add('hidden');
                }
                if (mobileDropdown && !mobileMulti.contains(e.target)) {
                    mobileDropdown.classList.add('hidden');
                }
            });
        }

    } catch (error) {
        console.error('Error loading libraries:', error);
        showToast('error', 'Failed to load music libraries');

        // Hide loading and show error state
        const desktopLoading = document.getElementById('library-loading');
        const mobileLoading = document.getElementById('mobile-library-loading');
        const desktopSingle = document.getElementById('library-single');
        const mobileSingle = document.getElementById('mobile-library-single');
        const desktopMulti = document.getElementById('library-multi');
        const mobileMulti = document.getElementById('mobile-library-multi');

        // Hide all states
        if (desktopLoading) desktopLoading.classList.add('hidden');
        if (mobileLoading) mobileLoading.classList.add('hidden');
        if (desktopSingle) desktopSingle.classList.add('hidden');
        if (mobileSingle) mobileSingle.classList.add('hidden');
        if (desktopMulti) desktopMulti.classList.add('hidden');
        if (mobileMulti) mobileMulti.classList.add('hidden');
    }
}

// Handle library selection change


// Update the display text for multi-library selector
function updateLibraryDisplayText() {
    const desktopText = document.getElementById('library-multi-text');
    const mobileText = document.getElementById('mobile-library-multi-text');

    if (selectedLibraryIds.length === 0) {
        if (desktopText) desktopText.textContent = 'Select library';
        if (mobileText) mobileText.textContent = 'Select library';
        if (desktopText) desktopText.className = 'text-gray-500 truncate';
        if (mobileText) mobileText.className = 'text-gray-500 truncate';
    } else if (selectedLibraryIds.length === 1) {
        const library = allLibraries.find(lib => lib.id === selectedLibraryIds[0]);
        const libraryName = library ? library.name : '1 library';
        if (desktopText) desktopText.textContent = libraryName;
        if (mobileText) mobileText.textContent = libraryName;
        if (desktopText) desktopText.className = 'text-gray-900 truncate';
        if (mobileText) mobileText.className = 'text-gray-900 truncate';
    } else {
        if (desktopText) desktopText.textContent = `${selectedLibraryIds.length} libraries`;
        if (mobileText) mobileText.textContent = `${selectedLibraryIds.length} libraries`;
        if (desktopText) desktopText.className = 'text-gray-900 truncate';
        if (mobileText) mobileText.className = 'text-gray-900 truncate';
    }
}

// Handle library checkbox changes
function handleLibraryCheckboxChange(e) {
    const libraryId = e.target.value;
    const isChecked = e.target.checked;

    if (isChecked) {
        if (!selectedLibraryIds.includes(libraryId)) {
            selectedLibraryIds.push(libraryId);
        }
    } else {
        selectedLibraryIds = selectedLibraryIds.filter(id => id !== libraryId);
    }

    // Sync checkboxes between desktop and mobile
    const desktopCheckbox = document.getElementById(`desktop-lib-${libraryId}`);
    const mobileCheckbox = document.getElementById(`mobile-lib-${libraryId}`);

    if (desktopCheckbox && desktopCheckbox !== e.target) {
        desktopCheckbox.checked = isChecked;
    }
    if (mobileCheckbox && mobileCheckbox !== e.target) {
        mobileCheckbox.checked = isChecked;
    }

    // Update localStorage
    localStorage.setItem('selectedLibraryIds', JSON.stringify(selectedLibraryIds));

    // Update display text
    updateLibraryDisplayText();

    // Refresh current page content if needed
    const currentPage = getPageFromURL(window.location.pathname);
    if (currentPage === 'this-is-artist') {
        loadArtists();
    } else if (currentPage === 'genre-mix') {
        loadGenres();
        loadMetaGenres();
    } else if (currentPage === 'genre-insights') {
        loadGenreInsights();
    }

    console.log(`📚 Library selection updated:`, selectedLibraryIds);
}

// Check if libraries are selected and show warning if not
function checkLibrarySelection() {
    if (selectedLibraryIds.length === 0) {
        showToast('warning', 'Please select a music library.');
        // Highlight the library selector
        const libraryMulti = document.getElementById('library-multi');
        const mobileLibraryMulti = document.getElementById('mobile-library-multi');
        if (libraryMulti) {
            libraryMulti.classList.add('ring-2', 'ring-red-500', 'ring-opacity-50');
            setTimeout(() => libraryMulti.classList.remove('ring-2', 'ring-red-500', 'ring-opacity-50'), 3000);
        }
        if (mobileLibraryMulti) {
            mobileLibraryMulti.classList.add('ring-2', 'ring-red-500', 'ring-opacity-50');
            setTimeout(() => mobileLibraryMulti.classList.remove('ring-2', 'ring-red-500', 'ring-opacity-50'), 3000);
        }
        return false;
    }
    return true;
}

// Handle artist selection change
function handleArtistSelection(e) {
    selectedArtistId = e.target.value;
    const submitBtn = document.getElementById('create-artist-playlist-btn');

    if (selectedArtistId) {
        submitBtn.disabled = false;
    } else {
        submitBtn.disabled = true;
    }
}

// This Is Artist form submission
document.getElementById('this-is-form').addEventListener('submit', function(e) {
    e.preventDefault();
    createArtistPlaylist();
});

// Genre Mix form submission
document.getElementById('genre-mix-form').addEventListener('submit', function(e) {
    e.preventDefault();
    createGenrePlaylist();
});

async function createArtistPlaylist() {
    const submitBtn = document.getElementById('create-artist-playlist-btn');

    if (!selectedArtistId) {
        showToast('error', 'Please select an artist first');
        return;
    }

    if (!checkLibrarySelection()) {
        return;
    }

    // Show loading toast
    showToast('loading', 'Creating your playlist...', 0);
    submitBtn.disabled = true;

    try {
        const refreshFrequency = document.querySelector('input[name="artist-refresh-frequency"]:checked').value;
        const playlistLength = document.querySelector('input[name="artist-playlist-length"]:checked').value;

        const response = await fetch('/api/create_playlist', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                artist_ids: [selectedArtistId],
                refresh_frequency: refreshFrequency,
                playlist_length: parseInt(playlistLength),
                library_ids: selectedLibraryIds
            })
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }));
            throw new Error(errorData.detail || 'Failed to create playlist');
        }

        const data = await response.json();

        // Track successful artist playlist creation with Rybbit
        if (typeof window.rybbit !== 'undefined') {
            const modelInfo = await getAIModelInfo();
            window.rybbit.event('This Is Playlist Created', {
                trackCount: data.songs ? data.songs.length : 0,
                refreshFrequency: refreshFrequency,
                aiModel: modelInfo.model,
                aiProvider: modelInfo.provider
            });
        }

        // Show success toast
        showToast('success', `Playlist created with ${data.songs ? data.songs.length : 0} tracks`);
        
        // Update playlist count in sidebar
        updatePlaylistCount();

    } catch (error) {
        console.error('Error creating playlist:', error);
        showToast('error', error.message);
    } finally {
        submitBtn.disabled = false;
    }
}

async function createGenrePlaylist() {
    const submitBtn = document.getElementById('create-genre-playlist-btn');
    const mode = getGenreSelectionMode();

    if (mode === 'meta') {
        if (!selectedMetaGenre) {
            showToast('error', 'Please select a meta-genre first');
            return;
        }
    } else if (!selectedGenre) {
        showToast('error', 'Please select a genre first');
        return;
    }

    if (!checkLibrarySelection()) {
        return;
    }

    // Show loading toast
    showToast('loading', 'Creating your playlist...', 0);
    submitBtn.disabled = true;

    try {
        const refreshFrequency = document.querySelector('input[name="genre-refresh-frequency"]:checked').value;
        const playlistLength = document.querySelector('input[name="genre-playlist-length"]:checked').value;
        const artistConcentration = parseFloat(document.getElementById('genre-artist-concentration')?.value || '0.35');
        const albumConcentration = parseFloat(document.getElementById('genre-album-concentration')?.value || '0.25');
        const llmPolish = document.getElementById('genre-llm-polish')?.checked ?? true;

        const response = await fetch('/api/create_genre_playlist', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                genre: mode === 'raw' ? selectedGenre : null,
                meta_genre: mode === 'meta' ? selectedMetaGenre : null,
                genre_selection_mode: mode,
                refresh_frequency: refreshFrequency,
                playlist_length: parseInt(playlistLength),
                library_ids: selectedLibraryIds,
                artist_concentration: artistConcentration,
                album_concentration: albumConcentration,
                llm_polish: llmPolish
            })
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }));
            throw new Error(errorData.detail || 'Failed to create playlist');
        }

        const data = await response.json();

        // Track successful genre playlist creation with Rybbit
        if (typeof window.rybbit !== 'undefined') {
            const modelInfo = await getAIModelInfo();
            window.rybbit.event('Genre Mix Playlist Created', {
                trackCount: data.songs ? data.songs.length : 0,
                refreshFrequency: refreshFrequency,
                genre: mode === 'meta' ? selectedMetaGenre : selectedGenre,
                aiModel: modelInfo.model,
                aiProvider: modelInfo.provider
            });
        }

        // Show success toast
        showToast('success', `Playlist created with ${data.songs ? data.songs.length : 0} tracks`);

        // Update playlist count in sidebar
        updatePlaylistCount();

    } catch (error) {
        console.error('Error creating playlist:', error);
        showToast('error', error.message);
    } finally {
        submitBtn.disabled = false;
    }
}

// Re-discover Weekly functionality
async function generateRediscoverWeekly() {
    const button = document.getElementById('rediscover-btn');

    if (!checkLibrarySelection()) {
        return;
    }

    // Show loading toast
    showToast('loading', 'Analyzing your listening history...', 0);
    button.disabled = true;

    try {
        // Use v2.0 create endpoint (generates and creates playlist in one step)
        const refreshFrequency = document.querySelector('input[name="rediscover-refresh-frequency"]:checked').value;
        const playlistLength = document.querySelector('input[name="rediscover-playlist-length"]:checked').value;

        const response = await fetch('/api/create-rediscover-playlist-v2', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                refresh_frequency: refreshFrequency,
                playlist_length: parseInt(playlistLength),
                library_ids: selectedLibraryIds
            })
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }));
            throw new Error(errorData.detail || 'Failed to create Re-Discover playlist');
        }

        const data = await response.json();

        // Track successful Re-Discover playlist creation
        if (typeof window.rybbit !== 'undefined') {
            const modelInfo = await getAIModelInfo();
            window.rybbit.event('Re-Discover Playlist Created', {
                trackCount: data.track_count,
                theme: data.theme,
                mode: data.mode,
                refreshFrequency: refreshFrequency,
                aiModel: modelInfo.model,
                aiProvider: modelInfo.provider,
                isFallback: data.is_fallback || false
            });
        }

        // Show success message
        const fallbackMsg = data.is_fallback ? ' (using fallback strategy)' : '';
        showToast('success', `Re-Discover playlist created! "${data.theme}" theme with ${data.track_count} tracks${fallbackMsg}`);

    } catch (error) {
        showToast('error', error.message);
    } finally {
        button.disabled = false;
    }
}

// Handle version selection changes (removed - always use v2.0)
function handleVersionChange() {
    const button = document.getElementById('rediscover-btn');
    button.textContent = 'Generate Re-Discover Playlist';
}

// Initialize button text
document.addEventListener('DOMContentLoaded', function() {
    // Set initial button text
    handleVersionChange();

    document.getElementById('lidarr-disambiguation-close')?.addEventListener('click', hideLidarrDisambiguationModal);
    document.getElementById('lidarr-disambiguation-modal')?.addEventListener('click', (event) => {
        if (event.target.id === 'lidarr-disambiguation-modal') {
            hideLidarrDisambiguationModal();
        }
    });
    document.getElementById('lidarr-bulk-close')?.addEventListener('click', hideBulkLidarrModal);
    document.getElementById('lidarr-bulk-cancel')?.addEventListener('click', hideBulkLidarrModal);
    document.getElementById('lidarr-bulk-submit')?.addEventListener('click', submitBulkLidarrAdd);
    document.getElementById('lidarr-bulk-modal')?.addEventListener('click', (event) => {
        if (event.target.id === 'lidarr-bulk-modal') {
            hideBulkLidarrModal();
        }
    });
});

// Update playlist count in sidebar
async function updatePlaylistCount() {
    try {
        const response = await fetch('/api/playlists');
        if (response.ok) {
            const playlists = await response.json();
            const count = playlists.length;
            
            // Update both desktop and mobile sidebar text
            const desktopText = document.getElementById('desktop-playlists-text');
            const mobileText = document.getElementById('mobile-playlists-text');
            
            if (count > 0) {
                if (desktopText) desktopText.textContent = `Playlists (${count})`;
                if (mobileText) mobileText.textContent = `Playlists (${count})`;
            } else {
                if (desktopText) desktopText.textContent = 'Playlists';
                if (mobileText) mobileText.textContent = 'Playlists';
            }
        }
    } catch (error) {
        console.error('Error fetching playlist count:', error);
        // Keep default text on error
    }
}

// Manage Playlists functionality
// Format next refresh as YYYY-MM-DD plus local time
function formatNextRefresh(nextRefreshTime) {
    const nextRefresh = new Date(nextRefreshTime);
    if (Number.isNaN(nextRefresh.getTime())) return 'None';

    const datePart = formatDisplayDate(nextRefreshTime);
    const hours = String(nextRefresh.getHours()).padStart(2, '0');
    const minutes = String(nextRefresh.getMinutes()).padStart(2, '0');
    return `${datePart} ${hours}:${minutes}`;
}

function captureOpenMissingDetails() {
    const container = document.getElementById('playlists-container');
    if (!container) {
        return new Set();
    }
    const openIds = new Set();
    container.querySelectorAll('details[data-missing-details][open]').forEach((el) => {
        if (el.id) {
            openIds.add(el.id);
        }
    });
    return openIds;
}

function restoreOpenMissingDetails(openIds) {
    openIds.forEach((id) => {
        const el = document.getElementById(id);
        if (el) {
            el.open = true;
        }
    });
}

async function loadPlaylists(options = {}) {
    const preserveOpen = Boolean(options.preserveOpenDetails);
    const loadingDiv = document.getElementById('playlists-loading');
    const containerDiv = document.getElementById('playlists-container');
    const openDetails = preserveOpen ? captureOpenMissingDetails() : null;

    setupPlaylistDeleteHandler();
    setupLidarrHandler();
    setupPlaylistManagementHandler();
    await fetchLidarrStatus();

    if (!preserveOpen) {
        loadingDiv.classList.remove('hidden');
    }
    containerDiv.innerHTML = '';

    try {
        const response = await fetch('/api/playlists');
        if (!response.ok) {
            throw new Error('Failed to load playlists');
        }

        let playlists = await response.json();
        
        loadingDiv.classList.add('hidden');
        
        // Filter duplicates by navidrome_playlist_id to address backend JOIN issue
        const seenIds = new Set();
        playlists = playlists.filter(playlist => {
            // Use navidrome_playlist_id as unique identifier
            const id = playlist.navidrome_playlist_id || playlist.id; // fallback to id if no navidrome id
            if (seenIds.has(id)) {
                return false; // duplicate, filter out
            }
            seenIds.add(id);
            return true; // unique, keep
        });
        
        if (playlists.length === 0) {
            containerDiv.innerHTML = `
                <div class="text-center p-8 text-gray-500">
                    <p class="text-lg mb-2">No playlists yet</p>
                    <p class="text-sm">Create your first playlist using the options in the sidebar!</p>
                </div>
            `;
            return;
        }

        renderPlaylists(playlists);
        if (openDetails && openDetails.size > 0) {
            restoreOpenMissingDetails(openDetails);
        }

    } catch (error) {
        console.error('Error loading playlists:', error);
        loadingDiv.classList.add('hidden');
        containerDiv.innerHTML = `
            <div class="text-center p-8 text-red-600">
                <p class="text-lg mb-2">Error loading playlists</p>
                <p class="text-sm">${error.message}</p>
            </div>
        `;
    }
}

function truncateText(text, maxLength) {
    if (!text) return '';
    return text.length > maxLength ? text.substring(0, maxLength) + '...' : text;
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function escapeAttr(text) {
    if (!text) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

function setupPlaylistDeleteHandler() {
    const container = document.getElementById('playlists-container');
    if (!container || container.dataset.deleteHandlerAttached === 'true') {
        return;
    }
    container.dataset.deleteHandlerAttached = 'true';
    container.addEventListener('click', (event) => {
        const button = event.target.closest('[data-action="delete-playlist"]');
        if (!button) {
            return;
        }
        event.preventDefault();
        const playlistId = Number(button.dataset.playlistId);
        const playlistName = button.dataset.playlistName || '';
        deletePlaylist(playlistId, playlistName);
    });
}

function setupLidarrHandler() {
    const container = document.getElementById('playlists-container');
    if (!container || container.dataset.lidarrHandlerAttached === 'true') {
        return;
    }
    container.dataset.lidarrHandlerAttached = 'true';
    container.addEventListener('click', (event) => {
        const button = event.target.closest('[data-action="lidarr-add"]');
        if (!button) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        const playlistId = Number(button.dataset.playlistId);
        const index = Number(button.dataset.index);
        const mode = button.dataset.mode;
        submitLidarrAdd(playlistId, index, mode);
    });
}

function setupPlaylistManagementHandler() {
    const container = document.getElementById('playlists-container');
    if (!container || container.dataset.playlistManagementHandlerAttached === 'true') {
        return;
    }
    container.dataset.playlistManagementHandlerAttached = 'true';
    container.addEventListener('click', (event) => {
        const actionEl = event.target.closest('[data-action]');
        if (!actionEl) {
            return;
        }

        const action = actionEl.dataset.action;
        if (![
            'toggle-playlist-details',
            'save-playlist-settings',
            'refresh-playlist-now',
            'lidarr-add-all',
        ].includes(action)) {
            return;
        }

        event.preventDefault();
        event.stopPropagation();
        const playlistId = Number(actionEl.dataset.playlistId);

        if (action === 'toggle-playlist-details') {
            if (expandedPlaylistIds.has(playlistId)) {
                expandedPlaylistIds.delete(playlistId);
            } else {
                expandedPlaylistIds.add(playlistId);
            }
            loadPlaylists({ preserveOpenDetails: true });
        } else if (action === 'save-playlist-settings') {
            savePlaylistSettings(playlistId);
        } else if (action === 'refresh-playlist-now') {
            refreshPlaylistNow(playlistId);
        } else if (action === 'lidarr-add-all') {
            showBulkLidarrModal(playlistId);
        }
    });
}

async function fetchLidarrStatus() {
    try {
        const response = await fetch('/api/lidarr/status');
        if (response.ok) {
            lidarrStatus = await response.json();
        }
    } catch (error) {
        console.warn('Could not load Lidarr status:', error);
    }

    const noteEl = document.getElementById('lidarr-integration-note');
    if (noteEl) {
        const show = lidarrStatus.enabled && lidarrStatus.configured && lidarrStatus.reachable;
        noteEl.classList.toggle('hidden', !show);
    }
}

function renderLidarrBadge(track) {
    const lidarr = track.lidarr || {};
    const status = lidarr.status;
    if (status === 'added_artist' || status === 'added_album' || status === 'monitored_album') {
        return '<span class="inline-block text-xs font-medium text-indigo-800 bg-indigo-100 rounded px-2 py-0.5 ml-1">In Lidarr</span>';
    }
    if (status === 'already_exists') {
        return '<span class="inline-block text-xs font-medium text-gray-700 bg-gray-200 rounded px-2 py-0.5 ml-1">Already in Lidarr</span>';
    }
    if (status === 'error' || status === 'not_found') {
        return '<span class="inline-block text-xs font-medium text-red-800 bg-red-100 rounded px-2 py-0.5 ml-1">Failed</span>';
    }
    return '';
}

function renderLidarrActions(playlistId, index, track) {
    if (!lidarrStatus.enabled || !lidarrStatus.configured || !lidarrStatus.reachable) {
        return '';
    }

    const lidarr = track.lidarr || {};
    if (['added_artist', 'added_album', 'monitored_album', 'already_exists'].includes(lidarr.status)) {
        return '';
    }

    const hasAlbum = Boolean(track.album && String(track.album).trim());
    const albumControl = hasAlbum
        ? `<button type="button" data-action="lidarr-add" data-mode="album" data-playlist-id="${playlistId}" data-index="${index}" class="text-xs font-medium text-indigo-700 hover:text-indigo-900 underline cursor-pointer border-none bg-transparent p-0">Add album</button>`
        : `<span class="text-xs text-gray-400" title="No album metadata — add artist instead">Add album</span>`;

    return `
        <span class="inline-flex flex-wrap items-center gap-x-2 gap-y-1 mt-1 not-prose">
            ${albumControl}
            <button type="button" data-action="lidarr-add" data-mode="artist" data-playlist-id="${playlistId}" data-index="${index}" class="text-xs font-medium text-indigo-700 hover:text-indigo-900 underline cursor-pointer border-none bg-transparent p-0">Add artist</button>
        </span>
    `;
}

function hideLidarrDisambiguationModal() {
    const modal = document.getElementById('lidarr-disambiguation-modal');
    if (modal) {
        modal.classList.add('hidden');
        modal.setAttribute('aria-hidden', 'true');
    }
    pendingLidarrPick = null;
}

function showLidarrDisambiguationModal(playlistId, index, mode, candidates) {
    const modal = document.getElementById('lidarr-disambiguation-modal');
    const listEl = document.getElementById('lidarr-disambiguation-list');
    const titleEl = document.getElementById('lidarr-disambiguation-title');
    if (!modal || !listEl) {
        return;
    }

    pendingLidarrPick = { playlistId, index, mode };
    const modeLabel = mode === 'album' ? 'album' : 'artist';
    if (titleEl) {
        titleEl.textContent = `Multiple ${modeLabel} matches — choose one`;
    }

    listEl.innerHTML = candidates.map((candidate, candidateIndex) => {
        const name = mode === 'album'
            ? `${candidate.album_title || 'Unknown album'} — ${candidate.artist_name || 'Unknown artist'}`
            : (candidate.artist_name || 'Unknown artist');
        const disambiguation = candidate.disambiguation
            ? `<span class="text-gray-500"> (${escapeHtml(candidate.disambiguation)})</span>`
            : '';
        return `
            <button
                type="button"
                data-candidate-index="${candidateIndex}"
                class="w-full text-left px-3 py-2 rounded-lg border border-gray-200 hover:bg-indigo-50 hover:border-indigo-200 text-sm"
            >
                ${escapeHtml(name)}${disambiguation}
            </button>
        `;
    }).join('');

    listEl.querySelectorAll('[data-candidate-index]').forEach((button) => {
        button.addEventListener('click', () => {
            const candidate = candidates[Number(button.dataset.candidateIndex)];
            if (!candidate || !pendingLidarrPick) {
                return;
            }
            const { playlistId: pid, index: idx, mode: pickMode } = pendingLidarrPick;
            hideLidarrDisambiguationModal();
            submitLidarrAdd(
                pid,
                idx,
                pickMode,
                candidate.foreign_artist_id || null,
                candidate.foreign_album_id || null,
            );
        });
    });

    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
}

async function submitLidarrAdd(playlistId, index, mode, foreignArtistId = null, foreignAlbumId = null) {
    showToast('loading', 'Adding to Lidarr...', 0);

    try {
        const payload = { index, mode };
        if (foreignArtistId) {
            payload.foreign_artist_id = foreignArtistId;
        }
        if (foreignAlbumId) {
            payload.foreign_album_id = foreignAlbumId;
        }

        const response = await fetch(`/api/playlists/${playlistId}/missing/lidarr`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

        const data = await response.json().catch(() => ({}));
        hideToast(currentToast);

        if (data.status === 'ambiguous') {
            showLidarrDisambiguationModal(playlistId, index, mode, data.candidates || []);
            return;
        }

        if (!response.ok) {
            throw new Error(data.detail || data.message || 'Failed to add to Lidarr');
        }

        if (data.status === 'added_artist') {
            showToast('success', `Added artist "${data.artist_name || 'artist'}" to Lidarr`);
        } else if (data.status === 'added_album') {
            showToast('success', `Added album "${data.album_title || 'album'}" to Lidarr`);
        } else if (data.status === 'already_exists') {
            showToast('success', 'Already in Lidarr');
        } else if (data.status === 'not_found') {
            showToast('error', data.message || 'No match found in Lidarr');
        } else {
            showToast('success', 'Lidarr updated');
        }

        loadPlaylists({ preserveOpenDetails: true });
    } catch (error) {
        hideToast(currentToast);
        console.error('Lidarr add failed:', error);
        showToast('error', error.message);
    }
}

async function savePlaylistSettings(playlistId) {
    const trackCountInput = document.getElementById(`playlist-track-count-${playlistId}`);
    const refreshSelect = document.getElementById(`playlist-refresh-frequency-${playlistId}`);
    const playlistLength = Number(trackCountInput?.value || 0);
    const refreshFrequency = refreshSelect?.value || 'none';

    if (!playlistLength || playlistLength < 1) {
        showToast('warning', 'Track count must be at least 1');
        return;
    }

    showToast('loading', 'Saving playlist settings...', 0);
    try {
        const response = await fetch(`/api/playlists/${playlistId}/settings`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                playlist_length: playlistLength,
                refresh_frequency: refreshFrequency,
            }),
        });
        const data = await response.json().catch(() => ({}));
        hideToast(currentToast);
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to save playlist settings');
        }
        showToast('success', 'Playlist settings saved');
        loadPlaylists({ preserveOpenDetails: true });
    } catch (error) {
        hideToast(currentToast);
        console.error('Failed to save playlist settings:', error);
        showToast('error', error.message);
    }
}

async function refreshPlaylistNow(playlistId) {
    showToast('loading', 'Updating playlist now...', 0);
    try {
        const response = await fetch(`/api/playlists/${playlistId}/refresh`, {
            method: 'POST',
        });
        const data = await response.json().catch(() => ({}));
        hideToast(currentToast);
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to update playlist');
        }
        showToast('success', 'Playlist updated');
        loadPlaylists({ preserveOpenDetails: true });
    } catch (error) {
        hideToast(currentToast);
        console.error('Failed to update playlist:', error);
        showToast('error', error.message);
    }
}

function showBulkLidarrModal(playlistId) {
    pendingBulkLidarrPlaylistId = playlistId;
    const modal = document.getElementById('lidarr-bulk-modal');
    if (!modal) {
        submitBulkLidarrAdd();
        return;
    }
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
}

function hideBulkLidarrModal() {
    const modal = document.getElementById('lidarr-bulk-modal');
    if (modal) {
        modal.classList.add('hidden');
        modal.setAttribute('aria-hidden', 'true');
    }
    pendingBulkLidarrPlaylistId = null;
}

async function submitBulkLidarrAdd() {
    if (!pendingBulkLidarrPlaylistId) {
        return;
    }

    const playlistId = pendingBulkLidarrPlaylistId;
    const payload = {
        search: Boolean(document.getElementById('bulk-lidarr-search')?.checked ?? true),
        monitor_only_target_album: Boolean(document.getElementById('bulk-lidarr-target-only')?.checked ?? true),
        skip_ambiguous: Boolean(document.getElementById('bulk-lidarr-skip-ambiguous')?.checked ?? true),
        prefer_album: Boolean(document.getElementById('bulk-lidarr-prefer-album')?.checked ?? true),
    };

    hideBulkLidarrModal();
    showToast('loading', 'Adding missing recommendations to Lidarr...', 0);
    try {
        const response = await fetch(`/api/playlists/${playlistId}/missing/lidarr/bulk`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        hideToast(currentToast);
        if (!response.ok) {
            throw new Error(data.detail || 'Failed to add missing recommendations to Lidarr');
        }
        const counts = data.counts || {};
        showToast(
            'success',
            `Lidarr updated: ${counts.added || 0} added/monitored, ${counts.already_exists || 0} already existed, ${counts.skipped || 0} skipped`
        );
        loadPlaylists({ preserveOpenDetails: true });
    } catch (error) {
        hideToast(currentToast);
        console.error('Bulk Lidarr add failed:', error);
        showToast('error', error.message);
    }
}

function getPlaylistTypeLabel(playlist) {
    const type = playlist.playlist_type;
    if (type === 'genre_mix') return 'Genre Mix';
    if (type === 'this_is') return 'This Is';
    if (type === 'rediscover' || type === 'rediscover_weekly_v2') return 'Re-Discover';
    if ((playlist.playlist_name || '').startsWith('Genre Mix:')) return 'Genre Mix';
    if (playlist.artist_id === 'rediscover' || playlist.artist_id === 'rediscover_v2') return 'Re-Discover';
    return 'This Is';
}

function getLidarrSummary(playlist) {
    const missing = playlist.recommended_missing || [];
    if (!missing.length) return 'None';
    const completed = missing.filter(track => {
        const status = track.lidarr?.status;
        return ['added_artist', 'added_album', 'monitored_album', 'already_exists'].includes(status);
    }).length;
    return `${completed}/${missing.length}`;
}

function renderRefreshFrequencyOptions(current) {
    const value = current || 'none';
    return ['none', 'daily', 'weekly', 'monthly'].map(option => {
        const label = option === 'none' ? 'Manual' : option.charAt(0).toUpperCase() + option.slice(1);
        return `<option value="${option}" ${value === option ? 'selected' : ''}>${label}</option>`;
    }).join('');
}

function renderMissingSuggestionRows(playlist) {
    const missing = playlist.recommended_missing || [];
    if (!missing.length) {
        return '<p class="text-sm text-gray-500 mb-0">No missing recommendations for this playlist.</p>';
    }

    return `
        <div class="border border-gray-200 rounded-lg overflow-hidden">
            <table class="min-w-full text-sm">
                <thead class="bg-gray-50 text-xs uppercase text-gray-500">
                    <tr>
                        <th class="text-left px-3 py-2">Track</th>
                        <th class="text-left px-3 py-2">Artist</th>
                        <th class="text-left px-3 py-2">Album</th>
                        <th class="text-left px-3 py-2">Lidarr</th>
                        <th class="text-left px-3 py-2">Actions</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-gray-100">
                    ${missing.map((track, index) => `
                        <tr>
                            <td class="px-3 py-2">
                                <span class="font-medium text-gray-900">${escapeHtml(track.title)}</span>
                                ${track.note ? `<p class="text-xs text-gray-500 mt-1 mb-0">${escapeHtml(track.note)}</p>` : ''}
                            </td>
                            <td class="px-3 py-2">${escapeHtml(track.artist)}</td>
                            <td class="px-3 py-2">${track.album ? escapeHtml(track.album) : '<span class="text-gray-400">Unknown</span>'}</td>
                            <td class="px-3 py-2">${renderLidarrBadge(track) || '<span class="text-xs text-gray-400">Pending</span>'}</td>
                            <td class="px-3 py-2">${renderLidarrActions(playlist.id, index, track)}</td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        </div>
    `;
}

function renderPlaylistDetailRow(playlist) {
    const songs = playlist.songs || [];
    const missing = playlist.recommended_missing || [];
    const refreshFrequency = playlist.refresh_frequency || 'none';
    const trackSummary = songs.length
        ? `${escapeHtml(songs.slice(0, 30).join(', '))}${songs.length > 30 ? '...' : ''}`
        : 'No tracks stored.';
    return `
        <tr class="bg-gray-50">
            <td colspan="9" class="px-4 py-4">
                <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
                    <section class="lg:col-span-2 bg-white border border-gray-200 rounded-lg p-4">
                        <div class="flex items-center justify-between gap-3 mb-3">
                            <h4 class="font-semibold text-gray-900 mb-0">Recommended but not in library (${missing.length})</h4>
                            <button
                                type="button"
                                data-action="lidarr-add-all"
                                data-playlist-id="${playlist.id}"
                                class="text-xs font-semibold rounded bg-indigo-600 text-white px-3 py-1.5 hover:bg-indigo-700 disabled:opacity-50"
                                ${missing.length && lidarrStatus.enabled && lidarrStatus.configured && lidarrStatus.reachable ? '' : 'disabled'}
                            >
                                Add all to Lidarr
                            </button>
                        </div>
                        ${renderMissingSuggestionRows(playlist)}
                    </section>
                    <section class="bg-white border border-gray-200 rounded-lg p-4">
                        <h4 class="font-semibold text-gray-900 mb-3">Playlist settings</h4>
                        <label class="block text-xs font-medium text-gray-600 mb-1" for="playlist-track-count-${playlist.id}">Track count</label>
                        <input
                            id="playlist-track-count-${playlist.id}"
                            type="number"
                            min="1"
                            max="500"
                            value="${playlist.playlist_length || playlist.track_count || 25}"
                            class="w-full border border-gray-300 rounded px-3 py-2 text-sm mb-3"
                        >
                        <label class="block text-xs font-medium text-gray-600 mb-1" for="playlist-refresh-frequency-${playlist.id}">Update rate</label>
                        <select
                            id="playlist-refresh-frequency-${playlist.id}"
                            class="w-full border border-gray-300 rounded px-3 py-2 text-sm mb-4"
                        >
                            ${renderRefreshFrequencyOptions(refreshFrequency)}
                        </select>
                        <div class="flex flex-wrap gap-2">
                            <button type="button" data-action="save-playlist-settings" data-playlist-id="${playlist.id}" class="text-sm font-semibold rounded bg-gray-900 text-white px-3 py-2 hover:bg-gray-700">Save settings</button>
                            <button type="button" data-action="refresh-playlist-now" data-playlist-id="${playlist.id}" class="text-sm font-semibold rounded border border-gray-300 px-3 py-2 hover:bg-gray-100">Update now</button>
                        </div>
                    </section>
                </div>
                <section class="mt-4 bg-white border border-gray-200 rounded-lg p-4">
                    <h4 class="font-semibold text-gray-900 mb-2">Details</h4>
                    ${playlist.reasoning ? `<p class="text-sm text-gray-600 italic">${escapeHtml(playlist.reasoning)}</p>` : '<p class="text-sm text-gray-500">No reasoning stored.</p>'}
                    <p class="text-xs text-gray-500 mb-2">Current tracks (${songs.length}):</p>
                    <p class="text-sm text-gray-700 mb-0">${trackSummary}</p>
                </section>
            </td>
        </tr>
    `;
}

function renderPlaylists(playlists) {
    const container = document.getElementById('playlists-container');
    const hasMissingFeature = playlists.some(
        p => (p.recommended_missing && p.recommended_missing.length) || (p.added_from_suggestions > 0)
    );
    const noteEl = document.getElementById('missing-recommendations-note');
    if (noteEl) {
        noteEl.classList.toggle('hidden', !hasMissingFeature);
    }

    const rows = playlists.map(playlist => {
        const isExpanded = expandedPlaylistIds.has(playlist.id);
        const missingCount = (playlist.recommended_missing || []).length;
        const addedBadge = playlist.added_from_suggestions > 0
            ? `<span class="block text-xs text-green-700">+${playlist.added_from_suggestions} from suggestions</span>`
            : '';
        return `
            <tr class="hover:bg-gray-50 cursor-pointer" data-action="toggle-playlist-details" data-playlist-id="${playlist.id}">
                <td class="px-4 py-3">
                    <div class="font-semibold text-gray-900">${escapeHtml(playlist.playlist_name)}</div>
                    ${addedBadge}
                </td>
                <td class="px-4 py-3 text-sm text-gray-700">${getPlaylistTypeLabel(playlist)}</td>
                <td class="px-4 py-3 text-sm text-gray-700">${playlist.track_count || 0}</td>
                <td class="px-4 py-3 text-sm text-gray-700">${missingCount}</td>
                <td class="px-4 py-3 text-sm text-gray-700">${getLidarrSummary(playlist)}</td>
                <td class="px-4 py-3 text-sm text-gray-700">${playlist.refresh_frequency || 'manual'}</td>
                <td class="px-4 py-3 text-sm text-gray-700">${playlist.next_refresh ? formatNextRefresh(playlist.next_refresh) : 'None'}</td>
                <td class="px-4 py-3 text-sm text-gray-700">${playlist.last_refreshed ? formatFriendlyDate(playlist.last_refreshed) : 'Never'}</td>
                <td class="px-4 py-3 text-right">
                    <button type="button" data-action="refresh-playlist-now" data-playlist-id="${playlist.id}" class="text-xs font-medium text-indigo-700 hover:text-indigo-900 underline mr-2">Update now</button>
                    <button type="button" data-action="delete-playlist" data-playlist-id="${playlist.id}" data-playlist-name="${escapeAttr(playlist.playlist_name)}" class="text-xs font-medium text-red-600 hover:text-red-800 underline">Delete</button>
                    <span class="inline-block ml-2 text-gray-400">${isExpanded ? '▲' : '▼'}</span>
                </td>
            </tr>
            ${isExpanded ? renderPlaylistDetailRow(playlist) : ''}
        `;
    }).join('');

    container.innerHTML = `
        <div class="overflow-x-auto border border-gray-200 rounded-lg">
            <table class="min-w-full divide-y divide-gray-200 bg-white">
                <thead class="bg-gray-50 text-xs uppercase text-gray-500">
                    <tr>
                        <th class="text-left px-4 py-3">Playlist</th>
                        <th class="text-left px-4 py-3">Type</th>
                        <th class="text-left px-4 py-3">Tracks</th>
                        <th class="text-left px-4 py-3">Missing</th>
                        <th class="text-left px-4 py-3">Lidarr</th>
                        <th class="text-left px-4 py-3">Rate</th>
                        <th class="text-left px-4 py-3">Next</th>
                        <th class="text-left px-4 py-3">Last</th>
                        <th class="text-right px-4 py-3">Actions</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-gray-100">
                    ${rows}
                </tbody>
            </table>
        </div>
    `;
}

async function deletePlaylist(playlistId, playlistName) {
    if (!confirm(`Are you sure you want to delete "${playlistName}"?\n\nThis will permanently remove the playlist from both Magic Lists and your Navidrome library.`)) {
        return;
    }

    try {
        const response = await fetch(`/api/playlists/${playlistId}`, {
            method: 'DELETE'
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }));
            throw new Error(errorData.detail || 'Failed to delete playlist');
        }

        // Reload the playlists list and update count
        loadPlaylists();
        updatePlaylistCount();

        // Show success toast - note that the backend may only delete locally if Navidrome deletion fails
        showToast('success', 'Playlist deleted from local database (check Navidrome if it still appears there)');

    } catch (error) {
        console.error('Error deleting playlist:', error);
        showToast('error', error.message);
    }
}

// System Check functionality
async function runSystemChecks() {
    const listContainer = document.getElementById('system-checks-list');
    const resultsContainer = document.getElementById('system-check-results');
    const successBanner = document.getElementById('success-banner');
    const errorBanner = document.getElementById('error-banner');
    const updateSettingsBtn = document.getElementById('update-settings-btn');
    const rerunBtn = document.getElementById('rerun-checks-btn');

    // Reset UI
    successBanner.classList.add('hidden');
    errorBanner.classList.add('hidden');
    updateSettingsBtn.classList.add('hidden');
    rerunBtn.disabled = true;
    rerunBtn.innerHTML = `
        <svg class="animate-spin h-4 w-4 text-gray-400 mr-2 inline" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <span class="text-gray-400">Running checks...</span>
    `;

    try {
        // Call backend health check endpoint
        const response = await fetch('/api/health-check');
        if (!response.ok) {
            throw new Error('Failed to run system checks');
        }

        const data = await response.json();
        
        // Display check results
        displaySystemChecks(data.checks);
        
        // Show appropriate banner and buttons
        if (data.all_passed) {
            successBanner.classList.remove('hidden');
            
            // Track Rybbit event
            if (typeof window.rybbit !== 'undefined') {
                window.rybbit.event('System Check Completed', {
                    status: 'all_passed',
                    checkCount: data.checks ? data.checks.length : 0
                });
            }
        } else {
            errorBanner.classList.remove('hidden');
            updateSettingsBtn.classList.remove('hidden');
            
            // Track specific failure events with Rybbit
            if (typeof window.rybbit !== 'undefined') {
                const failedChecks = data.checks.filter(check => check.status === 'error');
                
                window.rybbit.event('System Check Completed', {
                    status: 'failed',
                    checkCount: data.checks ? data.checks.length : 0,
                    failedCount: failedChecks.length
                });
                
                // Track specific failure types
                failedChecks.forEach(check => {
                    if (check.name.includes('URL Reachable')) {
                        window.rybbit.event('System Check Failed', { type: 'url_reachable' });
                    } else if (check.name.includes('Authentication')) {
                        window.rybbit.event('System Check Failed', { type: 'authentication' });
                    } else if (check.name.includes('Artists API')) {
                        window.rybbit.event('System Check Failed', { type: 'artists_api' });
                    } else if (check.name.includes('AI Provider')) {
                        window.rybbit.event('System Check Failed', { type: 'ai_provider' });
                    }
                });
            }
        }
        
    } catch (error) {
        console.error('System check error:', error);
        listContainer.innerHTML = `
            <div class="p-4 text-red-600 border border-red-300 rounded-lg bg-red-50">
                <p class="font-medium">Error running system checks</p>
                <p class="text-sm mt-1">${error.message}</p>
            </div>
        `;
        errorBanner.classList.remove('hidden');
    } finally {
        rerunBtn.disabled = false;
        rerunBtn.innerHTML = 'Re-run Checks';
    }
}

function displaySystemChecks(checks) {
    const container = document.getElementById('system-checks-list');
    
    container.innerHTML = checks.map(check => {
        const statusIcon = getStatusIcon(check.status);
        const statusColor = getStatusColor(check.status);
        const hasDetails = check.message || check.suggestion;
        
        return `
            <div class="border border-gray-200 rounded-lg overflow-hidden">
                <div class="p-4 ${hasDetails ? 'cursor-pointer' : ''}" ${hasDetails ? `onclick="toggleCheckDetails('${check.name.replace(/[^a-zA-Z0-9]/g, '')}')"` : ''}>
                    <div class="flex items-center justify-between">
                        <div class="flex items-center">
                            <div class="flex-shrink-0">
                                ${statusIcon}
                            </div>
                            <div class="ml-3">
                                <h3 class="text-sm font-medium text-gray-900">${check.name}</h3>
                                ${check.status !== 'success' ? `<p class="text-sm ${statusColor}">${getStatusText(check.status)}</p>` : ''}
                            </div>
                        </div>
                        ${hasDetails ? `
                            <div class="flex-shrink-0">
                                <svg class="w-5 h-5 text-gray-400 transform transition-transform rotate-90" id="chevron-${check.name.replace(/[^a-zA-Z0-9]/g, '')}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                                    <path fill-rule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" clip-rule="evenodd"/>
                                </svg>
                            </div>
                        ` : ''}
                    </div>
                </div>
                ${hasDetails ? `
                    <div class="hidden px-4 pb-4 pt-4 border-t border-gray-100 bg-gray-50" id="details-${check.name.replace(/[^a-zA-Z0-9]/g, '')}">
                        ${check.message ? `<p class="text-sm text-gray-600 mb-2">${check.message}</p>` : ''}
                        ${check.suggestion ? `<p class="text-sm text-blue-600 font-medium">${check.suggestion}</p>` : ''}
                    </div>
                ` : ''}
            </div>
        `;
    }).join('');
}

function toggleCheckDetails(checkId) {
    const detailsDiv = document.getElementById(`details-${checkId}`);
    const chevron = document.getElementById(`chevron-${checkId}`);
    
    if (detailsDiv.classList.contains('hidden')) {
        detailsDiv.classList.remove('hidden');
        chevron.classList.remove('rotate-90');
        chevron.classList.add('-rotate-90');
    } else {
        detailsDiv.classList.add('hidden');
        chevron.classList.remove('-rotate-90');
        chevron.classList.add('rotate-90');
    }
}

function getStatusIcon(status) {
    switch (status) {
        case 'success':
            return `<svg class="w-5 h-5 text-green-400" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/>
            </svg>`;
        case 'warning':
            return `<svg class="w-5 h-5 text-yellow-400" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/>
            </svg>`;
        case 'info':
            return `<svg class="w-5 h-5 text-blue-400" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd"/>
            </svg>`;
        case 'error':
            return `<svg class="w-5 h-5 text-red-400" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"/>
            </svg>`;
        default:
            return `<svg class="w-5 h-5 text-gray-400 animate-spin" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="m12 2v4l4-4h-4z"></path>
            </svg>`;
    }
}

function getStatusColor(status) {
    switch (status) {
        case 'success':
            return 'text-green-600';
        case 'warning':
            return 'text-yellow-600';
        case 'info':
            return 'text-blue-600';
        case 'error':
            return 'text-red-600';
        default:
            return 'text-gray-500';
    }
}

function getStatusText(status) {
    switch (status) {
        case 'success':
            return 'Success';
        case 'warning':
            return 'Warning';
        case 'info':
            return '';
        case 'error':
            return 'Failed';
        default:
            return 'Checking...';
    }
}

// URL management function
function updateURL(page) {
    let url = '/';
    
    // Map pages to URL paths
    switch(page) {
        case 'home':
            url = '/';
            break;
        case 'this-is-artist':
            url = '/this-is';
            break;
        case 're-discover':
            url = '/re-discover';
            break;
        case 'genre-mix':
            url = '/genre-mix';
            break;
        case 'playlists':
            url = '/playlists';
            break;
        case 'genre-insights':
            url = '/genre-insights';
            break;
        case 'system-check':
            url = '/system-check';
            break;
        default:
            url = '/';
    }
    
    // Update browser URL without page reload
    if (window.location.pathname !== url) {
        window.history.pushState({ page: page }, '', url);
    }
}

function navigateToHome() {
    // Navigate to home page (this will trigger a redirect to / which checks system status)
    window.location.href = '/';
}

function showSettingsHelp() {
    alert('To update your settings:\n\n1. Edit your .env file with the correct values\n2. Restart the application\n3. Run the system check again\n\nRefer to the SETUP.md file for detailed configuration instructions.');
}

// Auto-run system checks when the system-check page loads
function initSystemCheckPage() {
    runSystemChecks();
}

// URL ROUTING
// Handle browser back/forward navigation
window.addEventListener('popstate', function(event) {
    if (event.state && event.state.page) {
        // Use the stored page state
        handlePageNavigation(event.state.page);
    } else {
        // Determine page from URL
        const page = getPageFromURL(window.location.pathname);
        handlePageNavigation(page);
    }
});

// Get page from URL path
function getPageFromURL(pathname) {
    let page;
    switch(pathname) {
        case '/':
            page = 'home';
            break;
        case '/this-is':
            page = 'this-is-artist';
            break;
        case '/re-discover':
            page = 're-discover';
            break;
        case '/genre-mix':
            page = 'genre-mix';
            break;
        case '/playlists':
            page = 'playlists';
            break;
        case '/genre-insights':
            page = 'genre-insights';
            break;
        case '/system-check':
            page = 'system-check';
            break;
        default:
            page = 'home';
            break;
    }
    return page;
}

// Handle page navigation (used by both click and popstate)
function handlePageNavigation(page) {
    // Track page view with Rybbit
    if (typeof window.rybbit !== 'undefined') {
        window.rybbit.pageview();
    }

    // Map page to content
    let contentId;
    if (page === 'home') {
        contentId = 'welcome-content';
    } else if (page === 'this-is-artist') {
        contentId = 'this-is-content';
        // Load artists when navigating to This Is page (only if libraries selected)
        if (selectedLibraryIds.length > 0) {
            setTimeout(() => loadArtists(), 100);
        }
    } else if (page === 're-discover') {
        contentId = 'rediscover-content';
    } else if (page === 'genre-mix') {
        contentId = 'genre-mix-content';
        // Load genres when navigating to Genre Mix page (only if libraries selected)
        if (selectedLibraryIds.length > 0) {
            setTimeout(() => {
                loadGenres();
                loadMetaGenres();
            }, 100);
        }
    } else if (page === 'playlists') {
        contentId = 'manage-playlists-content';
        // Load playlists when navigating to manage page
        setTimeout(() => loadPlaylists(), 100);
    } else if (page === 'genre-insights') {
        contentId = 'genre-insights-content';
        setTimeout(() => loadGenreInsights(), 100);
    } else if (page === 'system-check') {
        contentId = 'system-check-content';
        // Auto-run system checks when navigating to system check page
        setTimeout(() => runSystemChecks(), 100);
    }

    setActiveMenuItem(page);
    showContent(contentId);
}


