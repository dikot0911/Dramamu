/**
 * MOBILE SIDEBAR TOGGLE - ULTRA SIMPLE VERSION
 */

(function() {
    'use strict';

    // Function to setup mobile sidebar
    function setupMobileSidebar() {
        const toggle = document.getElementById('mobileMenuToggle');
        const sidebar = document.getElementById('sidebarMobile');
        const overlay = document.getElementById('sidebarOverlay');
        const closeBtn = document.getElementById('sidebarMobileClose');

        if (!toggle || !sidebar || !overlay) {
            return false;
        }

        // Toggle on hamburger click
        toggle.onclick = function(e) {
            e.preventDefault();
            e.stopPropagation();
            sidebar.classList.toggle('active');
            overlay.classList.toggle('active');
        };

        // Close on overlay click
        overlay.onclick = function() {
            sidebar.classList.remove('active');
            overlay.classList.remove('active');
        };

        // Close on close button click
        if (closeBtn) {
            closeBtn.onclick = function(e) {
                e.preventDefault();
                e.stopPropagation();
                sidebar.classList.remove('active');
                overlay.classList.remove('active');
            };
        }

        // Close on menu item click
        const navItems = sidebar.querySelectorAll('.nav-item');
        navItems.forEach(item => {
            item.onclick = function() {
                sidebar.classList.remove('active');
                overlay.classList.remove('active');
            };
        });

        // Auto-close on resize to desktop
        window.onresize = function() {
            if (window.innerWidth > 992) {
                sidebar.classList.remove('active');
                overlay.classList.remove('active');
            }
        };

        return true;
    }

    // Wait for DOM to be ready and sidebar elements exist
    function waitAndSetup() {
        if (setupMobileSidebar()) {
            console.log('✅ Mobile sidebar ready');
            return;
        }

        // If elements not found yet, try again in 100ms
        setTimeout(waitAndSetup, 100);
    }

    // Start setup when DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', waitAndSetup);
    } else {
        waitAndSetup();
    }

})();
