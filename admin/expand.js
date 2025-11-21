/**
 * CARD EXPAND/COLLAPSE ANIMATION - DESKTOP & MOBILE OPTIMIZED
 * Smooth expand/collapse with device-specific animation optimization
 */

(function() {
    'use strict';

    // Breakpoint
    const DESKTOP_WIDTH = 992;

    // Device detection
    function isDesktop() {
        return window.innerWidth > DESKTOP_WIDTH;
    }

    function isMobile() {
        return window.innerWidth <= DESKTOP_WIDTH;
    }

    // Toggle card expand/collapse - Works on both desktop and mobile
    window.toggleCardExpand = function(button) {
        const card = button.closest('.card-expandable');
        if (!card) return;

        const grid = card.closest('.dashboard-grid');
        if (!grid) return;

        // If this card is expanded, collapse it
        if (card.classList.contains('card-expanded')) {
            card.classList.remove('card-expanded');
            grid.classList.remove('has-expanded');
            
            // On mobile, smooth scroll back up
            if (isMobile()) {
                setTimeout(() => {
                    card.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }, 100);
            }
            return;
        }

        // Collapse all other expanded cards
        const expandedCards = grid.querySelectorAll('.card-expanded');
        expandedCards.forEach(c => c.classList.remove('card-expanded'));

        // Expand this card
        card.classList.add('card-expanded');
        grid.classList.add('has-expanded');
        
        // On mobile, scroll to expanded card
        if (isMobile()) {
            setTimeout(() => {
                card.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }, 150);
        }
    };

    // Close expanded card on overlay/grid background click
    document.addEventListener('click', function(e) {
        // If clicking on grid background (not a card/button), close expanded
        if (e.target.classList.contains('dashboard-grid')) {
            const grid = e.target;
            const expanded = grid.querySelector('.card-expanded');
            if (expanded) {
                expanded.classList.remove('card-expanded');
                grid.classList.remove('has-expanded');
            }
        }
    });

    // Handle window resize
    window.addEventListener('resize', function() {
        // Close expanded cards when resizing below desktop width
        if (isMobile()) {
            const expandedCards = document.querySelectorAll('.card-expanded');
            expandedCards.forEach(card => {
                card.classList.remove('card-expanded');
                const grid = card.closest('.dashboard-grid');
                if (grid) {
                    grid.classList.remove('has-expanded');
                }
            });
        }
    });

    // Add touch event optimization on mobile
    if (isMobile()) {
        document.addEventListener('touchstart', function(e) {
            const expandBtn = e.target.closest('.card-expand-btn');
            if (expandBtn) {
                // Add active state for better touch feedback
                expandBtn.style.opacity = '1';
            }
        }, false);

        document.addEventListener('touchend', function(e) {
            const expandBtn = e.target.closest('.card-expand-btn');
            if (expandBtn) {
                expandBtn.style.opacity = '';
            }
        }, false);
    }

    console.log('✅ Card expand/collapse animation loaded (' + (isDesktop() ? 'Desktop' : 'Mobile') + ' optimized)');

})();
