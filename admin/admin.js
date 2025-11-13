const API_BASE_URL = window.location.origin;

const AdminPanel = {
    getToken() {
        return localStorage.getItem('admin_token');
    },

    setToken(token) {
        localStorage.setItem('admin_token', token);
    },

    clearToken() {
        localStorage.removeItem('admin_token');
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
        const token = this.getToken();
        const headers = {
            'Content-Type': 'application/json',
            ...options.headers
        };

        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }

        try {
            const response = await fetch(`${API_BASE_URL}${endpoint}`, {
                ...options,
                headers
            });

            const data = await response.json();

            if (!response.ok) {
                if (response.status === 401) {
                    this.clearToken();
                    window.location.href = '/panel/login.html';
                    throw new Error('Session expired. Please login again.');
                }
                throw new Error(data.detail || 'An error occurred');
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
        const token = this.getToken();
        if (!token) {
            window.location.href = '/panel/login.html';
            return null;
        }

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

    logout() {
        this.clearToken();
        window.location.href = '/panel/login.html';
    },

    formatDate(dateString) {
        if (!dateString) return '-';
        const date = new Date(dateString);
        return date.toLocaleDateString('id-ID', {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
    },

    formatCurrency(amount) {
        return new Intl.NumberFormat('id-ID', {
            style: 'currency',
            currency: 'IDR',
            minimumFractionDigits: 0
        }).format(amount);
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
            prev.textContent = 'Previous';
            prev.onclick = () => onPageChange(currentPage - 1);
            pagination.appendChild(prev);
        }

        const pageInfo = document.createElement('span');
        pageInfo.className = 'pagination-info';
        pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
        pagination.appendChild(pageInfo);

        if (currentPage < totalPages) {
            const next = document.createElement('button');
            next.className = 'btn btn-secondary';
            next.textContent = 'Next';
            next.onclick = () => onPageChange(currentPage + 1);
            pagination.appendChild(next);
        }

        return pagination;
    }
};

window.AdminPanel = AdminPanel;
