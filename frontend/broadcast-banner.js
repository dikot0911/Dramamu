// Broadcast Banner Component
// Menampilkan broadcast di bawah navbar bawah dan di atas konten utama

(function() {
    'use strict';

    let broadcastsData = [];
    let currentIndex = 0;
    let dismissedBroadcasts = JSON.parse(localStorage.getItem('dismissedBroadcasts') || '[]');

    async function fetchActiveBroadcasts() {
        try {
            const response = await fetch(`${window.CONFIG.API_BASE}/api/broadcasts/active`);
            if (!response.ok) {
                console.error('Failed to fetch broadcasts:', response.status);
                return;
            }
            
            const data = await response.json();
            broadcastsData = (data.broadcasts || []).filter(b => !dismissedBroadcasts.includes(b.id));
            
            if (broadcastsData.length > 0) {
                renderBroadcastBanner();
            }
        } catch (error) {
            console.error('Error fetching broadcasts:', error);
        }
    }

    function renderBroadcastBanner() {
        // Remove existing banner if any
        const existingBanner = document.getElementById('broadcast-banner-container');
        if (existingBanner) {
            existingBanner.remove();
        }

        if (broadcastsData.length === 0) return;

        const broadcast = broadcastsData[currentIndex];
        
        // Create banner container
        const bannerContainer = document.createElement('div');
        bannerContainer.id = 'broadcast-banner-container';
        bannerContainer.style.cssText = `
            position: sticky;
            top: 0;
            z-index: 100;
            width: 100%;
            background: linear-gradient(135deg, rgba(212, 175, 55, 0.15) 0%, rgba(212, 175, 55, 0.05) 100%);
            border-bottom: 1px solid rgba(212, 175, 55, 0.3);
            padding: 12px 16px;
            animation: slideDown 0.3s ease-out;
        `;

        // Create banner content
        const bannerHTML = `
            <style>
                @keyframes slideDown {
                    from {
                        transform: translateY(-100%);
                        opacity: 0;
                    }
                    to {
                        transform: translateY(0);
                        opacity: 1;
                    }
                }
                
                .broadcast-banner-content {
                    display: flex;
                    align-items: flex-start;
                    gap: 12px;
                }
                
                .broadcast-icon {
                    flex-shrink: 0;
                    width: 24px;
                    height: 24px;
                    color: #d4af37;
                    margin-top: 2px;
                }
                
                .broadcast-message {
                    flex: 1;
                    color: #E5E7EB;
                    font-size: 14px;
                    line-height: 1.5;
                }
                
                .broadcast-actions {
                    display: flex;
                    gap: 8px;
                    align-items: center;
                    flex-shrink: 0;
                }
                
                .broadcast-nav-btn {
                    background: rgba(212, 175, 55, 0.2);
                    border: 1px solid rgba(212, 175, 55, 0.3);
                    border-radius: 6px;
                    width: 28px;
                    height: 28px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    cursor: pointer;
                    transition: all 0.2s;
                }
                
                .broadcast-nav-btn:hover {
                    background: rgba(212, 175, 55, 0.3);
                    border-color: rgba(212, 175, 55, 0.5);
                }
                
                .broadcast-nav-btn:active {
                    transform: scale(0.95);
                }
                
                .broadcast-close-btn {
                    background: rgba(239, 68, 68, 0.2);
                    border: 1px solid rgba(239, 68, 68, 0.3);
                    border-radius: 6px;
                    width: 28px;
                    height: 28px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    cursor: pointer;
                    transition: all 0.2s;
                }
                
                .broadcast-close-btn:hover {
                    background: rgba(239, 68, 68, 0.3);
                    border-color: rgba(239, 68, 68, 0.5);
                }
                
                .broadcast-close-btn:active {
                    transform: scale(0.95);
                }
                
                .broadcast-counter {
                    font-size: 12px;
                    color: #9CA3AF;
                }
            </style>
            
            <div class="broadcast-banner-content">
                <svg class="broadcast-icon" fill="currentColor" viewBox="0 0 20 20">
                    <path d="M10 2a6 6 0 00-6 6v3.586l-.707.707A1 1 0 004 14h12a1 1 0 00.707-1.707L16 11.586V8a6 6 0 00-6-6zM10 18a3 3 0 01-3-3h6a3 3 0 01-3 3z"></path>
                </svg>
                
                <div class="broadcast-message">${escapeHtml(broadcast.message)}</div>
                
                <div class="broadcast-actions">
                    ${broadcastsData.length > 1 ? `
                        <div class="broadcast-counter">${currentIndex + 1}/${broadcastsData.length}</div>
                        <button class="broadcast-nav-btn" id="broadcast-prev">
                            <svg style="width: 16px; height: 16px;" fill="currentColor" viewBox="0 0 20 20">
                                <path fill-rule="evenodd" d="M12.707 5.293a1 1 0 010 1.414L9.414 10l3.293 3.293a1 1 0 01-1.414 1.414l-4-4a1 1 0 010-1.414l4-4a1 1 0 011.414 0z" clip-rule="evenodd"></path>
                            </svg>
                        </button>
                        <button class="broadcast-nav-btn" id="broadcast-next">
                            <svg style="width: 16px; height: 16px;" fill="currentColor" viewBox="0 0 20 20">
                                <path fill-rule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" clip-rule="evenodd"></path>
                            </svg>
                        </button>
                    ` : ''}
                    <button class="broadcast-close-btn" id="broadcast-close">
                        <svg style="width: 16px; height: 16px; color: #EF4444;" fill="currentColor" viewBox="0 0 20 20">
                            <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"></path>
                        </svg>
                    </button>
                </div>
            </div>
        `;

        bannerContainer.innerHTML = bannerHTML;

        // Insert banner after navbar (di bawah navbar bawah)
        const navbar = document.getElementById('navbar-container');
        if (navbar && navbar.nextSibling) {
            navbar.parentNode.insertBefore(bannerContainer, navbar.nextSibling);
        } else if (navbar) {
            navbar.parentNode.appendChild(bannerContainer);
        } else {
            // Fallback: insert at top of body
            document.body.insertBefore(bannerContainer, document.body.firstChild);
        }

        // Add event listeners
        const closeBtn = document.getElementById('broadcast-close');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => dismissBroadcast(broadcast.id));
        }

        if (broadcastsData.length > 1) {
            const prevBtn = document.getElementById('broadcast-prev');
            const nextBtn = document.getElementById('broadcast-next');
            
            if (prevBtn) {
                prevBtn.addEventListener('click', navigateBroadcast.bind(null, -1));
            }
            
            if (nextBtn) {
                nextBtn.addEventListener('click', navigateBroadcast.bind(null, 1));
            }
        }
    }

    function navigateBroadcast(direction) {
        currentIndex = (currentIndex + direction + broadcastsData.length) % broadcastsData.length;
        renderBroadcastBanner();
    }

    function dismissBroadcast(broadcastId) {
        // Add to dismissed list
        dismissedBroadcasts.push(broadcastId);
        localStorage.setItem('dismissedBroadcasts', JSON.stringify(dismissedBroadcasts));

        // Remove from current data
        broadcastsData = broadcastsData.filter(b => b.id !== broadcastId);
        
        // Update index if needed
        if (currentIndex >= broadcastsData.length) {
            currentIndex = Math.max(0, broadcastsData.length - 1);
        }

        // Re-render or remove
        if (broadcastsData.length > 0) {
            renderBroadcastBanner();
        } else {
            const banner = document.getElementById('broadcast-banner-container');
            if (banner) {
                banner.style.animation = 'slideUp 0.3s ease-out';
                setTimeout(() => banner.remove(), 300);
            }
        }
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', fetchActiveBroadcasts);
    } else {
        fetchActiveBroadcasts();
    }

    // Refresh broadcasts every 5 minutes
    setInterval(fetchActiveBroadcasts, 5 * 60 * 1000);

})();
