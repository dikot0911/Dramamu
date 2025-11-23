/**
 * DRAMAMU PREMIUM INITIALIZATION - OPTIMIZED
 * Simplified initialization dengan minimal event listeners
 */

// Mapping icon names to HTML elements across all pages
const iconMappings = {
    // Home page icons
    'film-icon': { icon: 'filmReel', size: '64', class: 'premium-icon-gold' },
    'sparkles-left': { icon: 'sparkles', size: '20', class: 'premium-icon-gold' },
    'sparkles-right': { icon: 'sparkles', size: '20', class: 'premium-icon-gold' },
    'menu-icon-film': { icon: 'film', size: '40', class: 'premium-icon-gold' },
    'menu-icon-grid': { icon: 'grid', size: '40', class: 'premium-icon-gold' },
    'menu-icon-money': { icon: 'money', size: '40', class: 'premium-icon-gold' },
    'menu-icon-diamond': { icon: 'diamond', size: '40', class: 'premium-icon-gold' },
    'menu-icon-film-reel': { icon: 'filmReel', size: '40', class: 'premium-icon-gold' },
    'menu-icon-chat': { icon: 'chat', size: '40', class: 'premium-icon-gold' },
    'menu-icon-star': { icon: 'star', size: '40', class: 'premium-icon-gold' },
    
    // Navigation icons (all pages)
    'nav-home': { icon: 'home', size: '28', class: '' },
    'nav-search': { icon: 'search', size: '28', class: '' },
    'nav-grid': { icon: 'grid', size: '28', class: '' },
    'nav-heart': { icon: 'heart', size: '28', class: '' },
    'nav-user': { icon: 'user', size: '28', class: '' },
    
    // Profil page icons
    'diamond-icon': { icon: 'diamond', size: '32', class: 'premium-icon-gold' },
    'heart-icon': { icon: 'heart', size: '28', class: 'premium-icon-gold' },
    'users-icon': { icon: 'user', size: '28', class: 'premium-icon-gold' },
    'money-icon': { icon: 'money', size: '28', class: 'premium-icon-gold' },
    'link-icon': { icon: 'link', size: '28', class: 'premium-icon-gold' },
    'film-request-icon': { icon: 'filmReel', size: '28', class: 'premium-icon-gold' },
    'chat-icon': { icon: 'chat', size: '28', class: 'premium-icon-gold' },
    
    // Favorit page icons
    'favorit-heart-icon': { icon: 'heart', size: '32', class: 'premium-icon-gold' },
    
    // Referal page icons
    'stats-icon': { icon: 'crown', size: '32', class: 'premium-icon-gold' },
    'copy-icon': { icon: 'link', size: '20', class: 'premium-icon-gold' },
    'wallet-icon': { icon: 'wallet', size: '32', class: 'premium-icon-gold' },
    
    // Payment icons
    'vip-icon': { icon: 'vip', size: '32', class: 'premium-icon-gold' },
    
    // Contact icons
    'contact-chat': { icon: 'chat', size: '48', class: 'premium-icon-gold' },
    'contact-star': { icon: 'star', size: '48', class: 'premium-icon-gold' }
};

/**
 * Initialize premium features when DOM is ready - OPTIMIZED
 */
function initPremiumFeatures() {
    // Guard to prevent double initialization
    if (window.premiumInitialized) {
        console.log('⚠️ Premium features already initialized, skipping...');
        return;
    }
    window.premiumInitialized = true;
    
    // Only inject SVG icons (minimal DOM manipulation)
    injectAllIcons();
    
    console.log('✨ Premium features initialized (optimized)');
}

/**
 * Inject all SVG icons based on mappings
 */
function injectAllIcons() {
    Object.keys(iconMappings).forEach(id => {
        const element = document.getElementById(id);
        if (element) {
            const mapping = iconMappings[id];
            injectPremiumIcon(`#${id}`, mapping.icon, mapping.class, mapping.size);
        }
    });
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPremiumFeatures);
} else {
    initPremiumFeatures();
}

console.log('🌟 Dramamu Premium Design System Loaded (Optimized)');
