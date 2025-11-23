/**
 * Mobile Expandable Card Animation
 * Simple vertical expand for mobile cards only
 */

(function() {
    'use strict';
    
    // Toggle mobile card expand
    window.toggleMobileCardExpand = function(card) {
        if (window.innerWidth > 992) return; // Desktop only
        
        // Close other expanded cards
        const allCards = document.querySelectorAll('.mobile-expandable-card');
        allCards.forEach(c => {
            if (c !== card) {
                c.classList.remove('expanded');
            }
        });
        
        // Toggle this card
        card.classList.toggle('expanded');
    };
    
    // Add click handlers to mobile cards
    document.addEventListener('DOMContentLoaded', function() {
        const mobileCards = document.querySelectorAll('.mobile-expandable-card');
        mobileCards.forEach(card => {
            card.addEventListener('click', function(e) {
                if (e.target.closest('.expand-toggle') || !e.target.closest('.chart-container')) {
                    window.toggleMobileCardExpand(card);
                }
            });
        });
        
        console.log('✅ Mobile card expand initialized');
    });
    
    // Close on resize to desktop
    window.addEventListener('resize', function() {
        if (window.innerWidth > 992) {
            const expandedCards = document.querySelectorAll('.mobile-expandable-card.expanded');
            expandedCards.forEach(card => card.classList.remove('expanded'));
        }
    });
    
})();
