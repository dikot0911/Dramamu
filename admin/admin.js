const API_BASE_URL = window.location.origin;

// Timezone handling - semua waktu dalam WIB (UTC+7)
// Backend nyimpen timestamp dalam UTC, frontend convert ke WIB
// Fungsi yang ada: formatDate(), formatDateLong(), formatRelativeTime()
// Untuk chart: pake toWIBDate() biar data dikelompokkan dengan bener

const AdminPanel = {
    init() {
        this.initTheme();
        this.initTruncatedIdClickHandler();
        this.startHeartbeat();
        // Mobile sidebar initialization dipindahkan ke sidebar.js untuk timing yang tepat
    },
    
    initMobileSidebar() {
        const mobileMenuToggle = document.getElementById('mobileMenuToggle');
        const sidebarMobile = document.getElementById('sidebarMobile');
        const sidebarOverlay = document.getElementById('sidebarOverlay');
        const sidebarMobileClose = document.getElementById('sidebarMobileClose');
        
        if (mobileMenuToggle && sidebarMobile && sidebarOverlay) {
            // Toggle sidebar when hamburger menu clicked
            mobileMenuToggle.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                sidebarMobile.classList.toggle('active');
                sidebarOverlay.classList.toggle('active');
            });
            
            // Close sidebar when overlay clicked
            sidebarOverlay.addEventListener('click', () => {
                sidebarMobile.classList.remove('active');
                sidebarOverlay.classList.remove('active');
            });
            
            // Close sidebar when close button clicked
            if (sidebarMobileClose) {
                sidebarMobileClose.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    sidebarMobile.classList.remove('active');
                    sidebarOverlay.classList.remove('active');
                });
            }
            
            
            // Auto-close if resize to desktop
            window.addEventListener('resize', () => {
                if (window.innerWidth > 992) {
                    sidebarMobile.classList.remove('active');
                    sidebarOverlay.classList.remove('active');
                }
            });
        }
    },
    
    async initMobileSidebarAdminUsers() {
        try {
            const user = await this.apiCall('/admin/me');
            const sidebarMobileContainer = document.getElementById('sidebarMobileAdminUsers');
            if (!sidebarMobileContainer) return;
            
            let activeAdmins = [];
            try {
                activeAdmins = await this.apiCall('/admin/active-admins');
            } catch (error) {
                console.error('Failed to load active admins:', error);
            }
            
            const otherAdmins = activeAdmins.filter(a => a.id !== user.id);
            
            let adminListHtml = '';
            if (otherAdmins.length > 0) {
                adminListHtml = otherAdmins.map(admin => {
                    const displayName = admin.display_name || admin.username;
                    const statusClass = admin.is_online ? 'online' : 'offline';
                    
                    let timeHtml = '';
                    if (!admin.is_online) {
                        let timeText = 'Belum pernah login';
                        if (admin.last_activity) {
                            timeText = this.formatRelativeTime(admin.last_activity);
                        }
                        timeHtml = `<div class="admin-user-time">${timeText}</div>`;
                    }
                    
                    return `
                        <div class="admin-user-item">
                            <div class="status-dot ${statusClass}"></div>
                            <div class="admin-user-details">
                                <div class="admin-user-name">${this.escapeHtml(displayName)}</div>
                                ${timeHtml}
                            </div>
                        </div>
                    `;
                }).join('');
            } else {
                adminListHtml = `
                    <div class="admin-user-item empty">
                        <div class="admin-user-details">
                            <div class="admin-user-name" style="color: rgba(255, 255, 255, 0.5); font-size: 12px;">Tidak ada admin lainnya</div>
                        </div>
                    </div>
                `;
            }
            
            const manageAdminButton = user.is_super_admin ? `
                <a href="admin-users.html" class="admin-users-menu-item">
                    <span data-icon="settings" data-icon-size="sm"></span>
                    <span>Kelola Admin</span>
                </a>
            ` : '';
            
            const adminUsersHtml = `
                <div class="sidebar-admin-users-mobile">
                    <div class="admin-users-header">
                        <span data-icon="users" data-icon-size="sm"></span>
                        <span>ADMIN AKTIF</span>
                    </div>
                    <div class="admin-users-list">${adminListHtml}</div>
                    ${manageAdminButton}
                </div>
            `;
            
            sidebarMobileContainer.innerHTML = adminUsersHtml;
            
            if (window.initIcons) {
                initIcons();
            }
            
            const sidebarMobile = document.getElementById('sidebarMobile');
            const sidebarOverlay = document.getElementById('sidebarOverlay');
            if (sidebarMobile && sidebarOverlay) {
                const adminLink = sidebarMobile.querySelector('.admin-users-menu-item');
                if (adminLink) {
                    adminLink.addEventListener('click', () => {
                        sidebarMobile.classList.remove('active');
                        sidebarOverlay.classList.remove('active');
                    });
                }
            }
        } catch (error) {
            console.error('Failed to load mobile sidebar admin users:', error);
            const sidebarMobileContainer = document.getElementById('sidebarMobileAdminUsers');
            if (sidebarMobileContainer) {
                sidebarMobileContainer.innerHTML = `
                    <div class="admin-user-item empty">
                        <div class="admin-user-details">
                            <div class="admin-user-name" style="color: rgba(255, 255, 255, 0.5); font-size: 12px;">Gagal memuat data admin</div>
                        </div>
                    </div>
                `;
            }
        }
    },
    
    startHeartbeat() {
        // Ping server every 2 minutes to keep session active
        // This ensures last_activity is always up-to-date
        setInterval(async () => {
            try {
                // Silent heartbeat - just update last_activity
                await this.apiCall('/admin/me', { skipAuthRedirect: true });
            } catch (error) {
                // Ignore errors - user might be on login page
            }
        }, 2 * 60 * 1000); // 2 minutes
    },
    
    initTruncatedIdClickHandler() {
        document.addEventListener('click', (e) => {
            const target = e.target.closest('.truncated-id[data-copy-text]');
            if (target) {
                const text = this.unescapeAttribute(target.getAttribute('data-copy-text'));
                this.copyToClipboard(text);
            }
        });
    },
    
    unescapeAttribute(text) {
        try {
            return decodeURIComponent(escape(atob(text)));
        } catch (e) {
            console.error('Invalid base64 in data attribute - potential XSS:', e);
            return '';
        }
    },

    initTheme() {
        const savedTheme = localStorage.getItem('admin_theme') || 'light';
        document.documentElement.setAttribute('data-theme', savedTheme);
    },

    toggleTheme() {
        const currentTheme = document.documentElement.getAttribute('data-theme');
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', newTheme);
        localStorage.setItem('admin_theme', newTheme);
        return newTheme;
    },

    getCurrentTheme() {
        return document.documentElement.getAttribute('data-theme') || 'light';
    },

    // Cookie-based auth: tokens are stored in HttpOnly cookies
    // These methods are kept for backward compatibility but do nothing
    getToken() {
        return null; // Tokens are in HttpOnly cookies, not accessible from JS
    },

    setToken(token) {
        // No-op: tokens are set as HttpOnly cookies by the backend
    },

    clearToken() {
        // Clear local storage for user data
        localStorage.removeItem('admin_user');
    },

    getUser() {
        const user = localStorage.getItem('admin_user');
        return user ? JSON.parse(user) : null;
    },

    setUser(user) {
        localStorage.setItem('admin_user', JSON.stringify(user));
    },

    async apiCall(endpoint, options = {}) {
        const headers = {
            'Content-Type': 'application/json',
            ...options.headers
        };

        // Cookie-based auth: no Bearer header needed, cookies sent automatically
        // Credentials must be included for cookies to be sent
        const fetchOptions = {
            ...options,
            headers,
            credentials: 'include' // Important: send cookies with request
        };

        // Allow login endpoint to handle 401 without auto-redirect
        const skipAuthRedirect = options.skipAuthRedirect || false;

        try {
            const response = await fetch(`${API_BASE_URL}${endpoint}`, fetchOptions);

            const data = await response.json();

            if (!response.ok) {
                if (response.status === 401 && !skipAuthRedirect) {
                    this.clearToken();
                    window.location.href = '/panel/login.html';
                    throw new Error('Session telah berakhir. Silakan login kembali.');
                }
                
                // Handle structured error responses
                if (typeof data.detail === 'object') {
                    const errorMsg = data.detail.error || data.detail.message || 'Terjadi kesalahan';
                    const remediation = data.detail.remediation || '';
                    throw new Error(remediation ? `${errorMsg}\n\n${remediation}` : errorMsg);
                }
                
                throw new Error(data.detail || 'Terjadi kesalahan');
            }

            return data;
        } catch (error) {
            throw error;
        }
    },

    showToast(message, type = 'success') {
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.innerHTML = `
            <div class="toast-content">
                <span class="toast-icon">${type === 'success' ? '✓' : '✕'}</span>
                <span class="toast-message">${message}</span>
            </div>
        `;
        
        document.body.appendChild(toast);
        
        setTimeout(() => toast.classList.add('show'), 10);
        
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    },

    showLoading(element) {
        element.disabled = true;
        element.dataset.originalText = element.innerHTML;
        element.innerHTML = '<span class="spinner"></span>';
    },

    hideLoading(element) {
        element.disabled = false;
        element.innerHTML = element.dataset.originalText;
    },

    async checkAuth() {
        // Cookie-based auth: check by calling /admin/me
        try {
            const user = await this.apiCall('/admin/me');
            this.setUser(user);
            return user;
        } catch (error) {
            this.clearToken();
            window.location.href = '/panel/login.html';
            return null;
        }
    },

    async logout() {
        try {
            // Call backend logout endpoint to clear cookies
            await this.apiCall('/admin/logout', { method: 'POST' });
        } catch (error) {
            console.error('Logout error:', error);
        }
        this.clearToken();
        window.location.href = '/panel/login.html';
    },

    formatDate(dateString) {
        if (!dateString) return '-';
        const date = new Date(dateString);
        if (isNaN(date.getTime())) return '-';
        
        const formatter = new Intl.DateTimeFormat('id-ID', {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            timeZone: 'Asia/Jakarta'
        });
        return formatter.format(date) + ' WIB';
    },

    formatDateLong(dateString) {
        if (!dateString) return '-';
        const date = new Date(dateString);
        if (isNaN(date.getTime())) return '-';
        
        const formatter = new Intl.DateTimeFormat('id-ID', {
            weekday: 'long',
            year: 'numeric',
            month: 'long',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            timeZone: 'Asia/Jakarta'
        });
        return formatter.format(date) + ' WIB';
    },

    formatRelativeTime(dateString) {
        if (!dateString) return '-';
        
        const nowWIB = this.getNowWIB();
        const now = nowWIB.getTime();
        const date = new Date(dateString);
        if (isNaN(date.getTime())) return '-';
        
        const diffMs = now - date.getTime();
        const diffSeconds = Math.floor(diffMs / 1000);
        
        if (diffSeconds < 0) return 'baru saja';
        if (diffSeconds < 60) return `${diffSeconds} detik yang lalu`;
        
        const diffMinutes = Math.floor(diffSeconds / 60);
        if (diffMinutes < 60) return `${diffMinutes} menit yang lalu`;
        
        const diffHours = Math.floor(diffMinutes / 60);
        if (diffHours < 24) return `${diffHours} jam yang lalu`;
        
        const diffDays = Math.floor(diffHours / 24);
        if (diffDays < 30) return `${diffDays} hari yang lalu`;
        
        const diffMonths = Math.floor(diffDays / 30);
        if (diffMonths < 12) return `${diffMonths} bulan yang lalu`;
        
        const diffYears = Math.floor(diffDays / 365);
        return `${diffYears} tahun yang lalu`;
    },

    isOnline(lastActivityString) {
        if (!lastActivityString) return false;
        
        const nowWIB = this.getNowWIB();
        const now = nowWIB.getTime();
        const lastActivity = new Date(lastActivityString);
        if (isNaN(lastActivity.getTime())) return false;
        
        const diffMs = now - lastActivity.getTime();
        const diffMinutes = Math.floor(diffMs / (1000 * 60));
        return diffMinutes < 5;
    },

    toWIBDate(dateString) {
        if (!dateString) return null;
        
        const date = new Date(dateString);
        if (isNaN(date.getTime())) return null;
        
        const formatter = new Intl.DateTimeFormat('en-US', {
            timeZone: 'Asia/Jakarta',
            year: 'numeric',
            month: '2-digit',
            day: '2-digit'
        });
        
        const parts = formatter.formatToParts(date);
        const year = parseInt(parts.find(p => p.type === 'year').value);
        const month = parseInt(parts.find(p => p.type === 'month').value) - 1;
        const day = parseInt(parts.find(p => p.type === 'day').value);
        
        return new Date(Date.UTC(year, month, day, 0, 0, 0, 0));
    },

    getNowWIB() {
        return new Date();
    },

    formatCurrency(amount) {
        return new Intl.NumberFormat('id-ID', {
            style: 'currency',
            currency: 'IDR',
            minimumFractionDigits: 0
        }).format(amount);
    },

    truncateId(id, maxLength = 8) {
        if (!id) return '-';
        const idStr = String(id);
        if (idStr.length <= maxLength) return idStr;
        return idStr.substring(0, maxLength) + '...';
    },

    formatIdWithCopy(id, maxLength = 8) {
        if (!id) return '-';
        const idStr = String(id);
        const truncated = this.truncateId(id, maxLength);
        
        if (idStr.length <= maxLength) {
            return `<span class="truncated-id">${this.escapeHtml(truncated)}</span>`;
        }
        
        const escapedId = this.escapeHtml(idStr);
        const escapedTruncated = this.escapeHtml(truncated);
        const escapedForAttr = this.escapeAttribute(idStr);
        return `<span class="truncated-id" title="${escapedId}" data-copy-text="${escapedForAttr}" style="cursor: pointer;">${escapedTruncated}</span>`;
    },
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },
    
    escapeAttribute(text) {
        try {
            return btoa(unescape(encodeURIComponent(String(text))));
        } catch (e) {
            console.error('Failed to encode attribute:', e);
            return '';
        }
    },

    copyToClipboard(text) {
        navigator.clipboard.writeText(text).then(() => {
            this.showToast('ID berhasil disalin!', 'success');
        }).catch(() => {
            this.showToast('Gagal menyalin ID', 'error');
        });
    },

    confirmAction(message) {
        return confirm(message);
    },

    createPagination(currentPage, totalPages, onPageChange) {
        const pagination = document.createElement('div');
        pagination.className = 'pagination';

        if (currentPage > 1) {
            const prev = document.createElement('button');
            prev.className = 'btn btn-secondary';
            prev.textContent = 'Sebelumnya';
            prev.onclick = () => onPageChange(currentPage - 1);
            pagination.appendChild(prev);
        }

        const pageInfo = document.createElement('span');
        pageInfo.className = 'pagination-info';
        pageInfo.textContent = `Halaman ${currentPage} dari ${totalPages}`;
        pagination.appendChild(pageInfo);

        if (currentPage < totalPages) {
            const next = document.createElement('button');
            next.className = 'btn btn-secondary';
            next.textContent = 'Selanjutnya';
            next.onclick = () => onPageChange(currentPage + 1);
            pagination.appendChild(next);
        }

        return pagination;
    },

    // Ultra Compact Chart Configuration (Screenshot-based)
    getChartConfig(isDark) {
        return {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: true,
                    position: 'bottom',
                    labels: {
                        color: isDark ? '#cbd5e1' : '#4a5568',
                        font: {
                            family: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
                            size: 10,
                            weight: 500
                        },
                        padding: 8,
                        usePointStyle: true,
                        pointStyle: 'circle'
                    }
                },
                tooltip: {
                    backgroundColor: isDark ? '#1e293b' : '#ffffff',
                    titleColor: isDark ? '#f1f5f9' : '#1a202c',
                    bodyColor: isDark ? '#cbd5e1' : '#4a5568',
                    borderColor: isDark ? '#334155' : '#e2e8f0',
                    borderWidth: 1,
                    padding: 8,
                    cornerRadius: 6,
                    titleFont: {
                        size: 11,
                        weight: 600
                    },
                    bodyFont: {
                        size: 10
                    },
                    displayColors: false
                }
            },
            scales: {
                x: {
                    ticks: {
                        color: isDark ? '#94a3b8' : '#718096',
                        font: {
                            size: 10
                        },
                        maxRotation: 45,
                        minRotation: 0
                    },
                    grid: {
                        color: isDark ? 'rgba(51, 65, 85, 0.3)' : 'rgba(226, 232, 240, 0.5)',
                        drawBorder: false,
                        lineWidth: 1
                    }
                },
                y: {
                    ticks: {
                        color: isDark ? '#94a3b8' : '#718096',
                        font: {
                            size: 10
                        },
                        precision: 0
                    },
                    grid: {
                        color: isDark ? 'rgba(51, 65, 85, 0.3)' : 'rgba(226, 232, 240, 0.5)',
                        drawBorder: false,
                        lineWidth: 1
                    }
                }
            },
            elements: {
                line: {
                    tension: 0.3,
                    borderWidth: 2
                },
                point: {
                    radius: 3,
                    hitRadius: 8,
                    hoverRadius: 4,
                    borderWidth: 2
                }
            }
        };
    },

    // Donut Chart Config (Ultra Compact)
    getDonutChartConfig(isDark) {
        return {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '65%',
            plugins: {
                legend: {
                    display: true,
                    position: 'bottom',
                    labels: {
                        color: isDark ? '#cbd5e1' : '#4a5568',
                        font: {
                            family: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
                            size: 10,
                            weight: 500
                        },
                        padding: 6,
                        usePointStyle: true,
                        pointStyle: 'circle',
                        boxWidth: 8,
                        boxHeight: 8
                    }
                },
                tooltip: {
                    backgroundColor: isDark ? '#1e293b' : '#ffffff',
                    titleColor: isDark ? '#f1f5f9' : '#1a202c',
                    bodyColor: isDark ? '#cbd5e1' : '#4a5568',
                    borderColor: isDark ? '#334155' : '#e2e8f0',
                    borderWidth: 1,
                    padding: 8,
                    cornerRadius: 6,
                    titleFont: {
                        size: 11,
                        weight: 600
                    },
                    bodyFont: {
                        size: 10
                    },
                    displayColors: true
                }
            }
        };
    },

    async initSidebarUserInfo() {
        try {
            const user = await this.apiCall('/admin/me');
            const sidebarContainer = document.querySelector('.sidebar');
            if (!sidebarContainer) return;

            // Get active admins
            let activeAdmins = [];
            try {
                activeAdmins = await this.apiCall('/admin/active-admins');
            } catch (error) {
                console.error('Failed to load active admins:', error);
            }

            // Filter out current user from admin list
            const otherAdmins = activeAdmins.filter(a => a.id !== user.id);

            // Show admin section for ALL admins (but button only for super admin)
            let adminListHtml = '';
            if (otherAdmins.length > 0) {
                adminListHtml = otherAdmins.map(admin => {
                    const displayName = admin.display_name || admin.username;
                    const statusClass = admin.is_online ? 'online' : 'offline';
                    
                    // Hanya tampilkan waktu jika admin TIDAK online (offline)
                    let timeHtml = '';
                    if (!admin.is_online) {
                        // Admin offline, tampilkan waktu terakhir aktif
                        let timeText = 'Belum pernah login';
                        if (admin.last_activity) {
                            timeText = this.formatRelativeTime(admin.last_activity);
                        }
                        timeHtml = `<div class="admin-user-time">${timeText}</div>`;
                    }
                    // Jika admin online, tidak tampilkan waktu sama sekali
                    
                    return `
                        <div class="admin-user-item">
                            <div class="status-dot ${statusClass}"></div>
                            <div class="admin-user-details">
                                <div class="admin-user-name">${this.escapeHtml(displayName)}</div>
                                ${timeHtml}
                            </div>
                        </div>
                    `;
                }).join('');
            } else {
                adminListHtml = `
                    <div class="admin-user-item empty">
                        <div class="admin-user-details">
                            <div class="admin-user-name" style="color: rgba(255, 255, 255, 0.5); font-size: 11px;">Tidak ada admin lainnya</div>
                        </div>
                    </div>
                `;
            }

            // Button "Kelola Admin" hanya untuk super admin
            const manageAdminButton = user.is_super_admin ? `
                <a href="admin-users.html" class="admin-users-menu-item">
                    <span data-icon="settings" data-icon-size="sm"></span>
                    <span>Kelola Admin</span>
                </a>
            ` : '';

            const userInfoHtml = `
                <div class="sidebar-admin-users">
                    <div class="admin-users-header">
                        <span data-icon="users" data-icon-size="sm"></span>
                        <span>ADMIN AKTIF</span>
                    </div>
                    <div class="admin-users-list">${adminListHtml}</div>
                    ${manageAdminButton}
                </div>
            `;

            if (userInfoHtml) {
                sidebarContainer.insertAdjacentHTML('beforeend', userInfoHtml);
            }

            // Initialize icons
            if (window.initIcons) {
                initIcons();
            }

            // Hide "Admin Users" from main navigation (now in admin section)
            const adminUsersMenuItem = document.querySelector('.nav-item[href="admin-users.html"]');
            if (adminUsersMenuItem) {
                adminUsersMenuItem.style.display = 'none';
            }
        } catch (error) {
            console.error('Failed to load user info:', error);
        }
    },

    autoRefreshInterval: null,
    autoRefreshEnabled: true,

    async fetchPendingCounts() {
        try {
            const data = await this.apiCall('/admin/stats/pending-counts');
            return data;
        } catch (error) {
            console.error('Failed to fetch pending counts:', error);
            return null;
        }
    },

    updateNotificationBadges(data) {
        if (!data) return;

        const requestBadge = document.querySelector('.request-notification-badge');
        if (requestBadge) {
            if (data.pending_requests > 0) {
                requestBadge.textContent = data.pending_requests > 99 ? '99+' : data.pending_requests;
                requestBadge.style.display = 'flex';
            } else {
                requestBadge.style.display = 'none';
            }
        }

        const withdrawalBadge = document.querySelector('.withdrawal-notification-badge');
        if (withdrawalBadge) {
            if (data.pending_withdrawals > 0) {
                withdrawalBadge.textContent = data.pending_withdrawals > 99 ? '99+' : data.pending_withdrawals;
                withdrawalBadge.style.display = 'flex';
            } else {
                withdrawalBadge.style.display = 'none';
            }
        }
    },

    updateDashboardStats(data) {
        if (!data) return;

        const statsElements = {
            totalUsers: document.getElementById('totalUsers'),
            vipUsers: document.getElementById('vipUsers'),
            totalMovies: document.getElementById('totalMovies'),
            pendingRequests: document.getElementById('pendingRequests'),
            pendingWithdrawals: document.getElementById('pendingWithdrawals'),
            totalRevenue: document.getElementById('totalRevenue')
        };

        if (statsElements.totalUsers) statsElements.totalUsers.textContent = data.total_users || 0;
        if (statsElements.vipUsers) statsElements.vipUsers.textContent = data.vip_users || 0;
        if (statsElements.totalMovies) statsElements.totalMovies.textContent = data.total_movies || 0;
        if (statsElements.pendingRequests) statsElements.pendingRequests.textContent = data.pending_requests || 0;
        if (statsElements.pendingWithdrawals) statsElements.pendingWithdrawals.textContent = data.pending_withdrawals || 0;
        if (statsElements.totalRevenue) {
            statsElements.totalRevenue.textContent = `Rp ${(data.total_revenue || 0).toLocaleString('id-ID')}`;
        }
    },

    async performAutoRefresh() {
        const data = await this.fetchPendingCounts();
        if (data) {
            this.updateNotificationBadges(data);
            this.updateDashboardStats(data);

            window.dispatchEvent(new CustomEvent('admin-stats-updated', { detail: data }));
        }
    },

    startAutoRefresh(intervalMs = 3000) {
        if (this.autoRefreshInterval) {
            clearInterval(this.autoRefreshInterval);
        }

        this.performAutoRefresh();

        this.autoRefreshInterval = setInterval(() => {
            if (this.autoRefreshEnabled) {
                this.performAutoRefresh();
            }
        }, intervalMs);
    },

    stopAutoRefresh() {
        if (this.autoRefreshInterval) {
            clearInterval(this.autoRefreshInterval);
            this.autoRefreshInterval = null;
        }
    },

    enableAutoRefresh() {
        this.autoRefreshEnabled = true;
    },

    disableAutoRefresh() {
        this.autoRefreshEnabled = false;
    }
};

window.AdminPanel = AdminPanel;
AdminPanel.init();
