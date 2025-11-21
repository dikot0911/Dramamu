/**
 * ThemeToggle - Self-contained theme toggle system
 * No dependencies, no race conditions, always works
 */

const ThemeToggle = {
    // Built-in SVG icons (no dependency on icons.js)
    icons: {
        sun: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="5"></circle>
            <line x1="12" y1="1" x2="12" y2="3"></line>
            <line x1="12" y1="21" x2="12" y2="23"></line>
            <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line>
            <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line>
            <line x1="1" y1="12" x2="3" y2="12"></line>
            <line x1="21" y1="12" x2="23" y2="12"></line>
            <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line>
            <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line>
        </svg>`,
        moon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
        </svg>`
    },

    buttonElement: null,

    /**
     * Initialize theme toggle system
     */
    init() {
        // Set initial theme from localStorage
        const savedTheme = localStorage.getItem('admin_theme') || 'light';
        document.documentElement.setAttribute('data-theme', savedTheme);

        // Wait for DOM to be ready
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => this.setupButton());
        } else {
            this.setupButton();
        }
    },

    /**
     * Setup the toggle button
     */
    setupButton() {
        // Find the button container
        const container = document.getElementById('themeToggleContainer');
        if (!container) {
            console.warn('ThemeToggle: Container #themeToggleContainer not found');
            return;
        }

        // Create button HTML
        const currentTheme = document.documentElement.getAttribute('data-theme') || 'light';
        const iconSvg = currentTheme === 'dark' ? this.icons.sun : this.icons.moon;

        container.innerHTML = `
            <button class="theme-toggle" id="themeToggle" title="Toggle tema (${currentTheme === 'dark' ? 'Terang' : 'Gelap'})">
                <span class="theme-toggle-icon">${iconSvg}</span>
            </button>
        `;

        // Get button reference
        this.buttonElement = document.getElementById('themeToggle');

        // Attach click handler
        if (this.buttonElement) {
            this.buttonElement.addEventListener('click', () => this.toggle());
        }
    },

    /**
     * Toggle theme
     */
    toggle() {
        const currentTheme = document.documentElement.getAttribute('data-theme') || 'light';
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

        // Update theme
        document.documentElement.setAttribute('data-theme', newTheme);
        localStorage.setItem('admin_theme', newTheme);

        // Update icon
        this.updateIcon(newTheme);

        // Animate button
        if (this.buttonElement) {
            this.buttonElement.style.transform = 'rotate(360deg)';
            setTimeout(() => {
                this.buttonElement.style.transform = '';
            }, 300);
        }
    },

    /**
     * Update icon based on theme
     */
    updateIcon(theme) {
        if (!this.buttonElement) return;

        const iconContainer = this.buttonElement.querySelector('.theme-toggle-icon');
        if (!iconContainer) return;

        const iconSvg = theme === 'dark' ? this.icons.sun : this.icons.moon;
        iconContainer.innerHTML = iconSvg;

        // Update title
        this.buttonElement.setAttribute('title', `Toggle tema (${theme === 'dark' ? 'Terang' : 'Gelap'})`);
    },

    /**
     * Get current theme
     */
    getCurrentTheme() {
        return document.documentElement.getAttribute('data-theme') || 'light';
    }
};

// Auto-initialize
ThemeToggle.init();

// Make available globally
window.ThemeToggle = ThemeToggle;
