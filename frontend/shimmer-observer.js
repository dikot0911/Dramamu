class ShimmerAnimationObserver {
    constructor() {
        this.observer = null;
        this.init();
    }

    init() {
        if (typeof IntersectionObserver === 'undefined') {
            console.warn('IntersectionObserver not supported');
            return;
        }

        const options = {
            root: null,
            rootMargin: '50px',
            threshold: 0
        };

        this.observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                const element = entry.target;
                
                if (entry.isIntersecting) {
                    element.classList.add('shimmer-visible');
                } else {
                    element.classList.remove('shimmer-visible');
                }
            });
        }, options);

        this.observeShimmerElements();
    }

    observeShimmerElements() {
        const selectors = [
            '.shimmer-card',
            '.shimmer-text',
            '.search-input-container',
            '.shimmer-box',
            '.member-card.vip'
        ];

        selectors.forEach(selector => {
            const elements = document.querySelectorAll(selector);
            elements.forEach(element => {
                if (this.observer) {
                    this.observer.observe(element);
                }
            });
        });
    }

    observe(element) {
        if (this.observer && element) {
            this.observer.observe(element);
        }
    }

    unobserve(element) {
        if (this.observer && element) {
            this.observer.unobserve(element);
        }
    }

    disconnect() {
        if (this.observer) {
            this.observer.disconnect();
        }
    }

    refresh() {
        if (this.observer) {
            this.observer.disconnect();
        }
        this.init();
    }
}

let shimmerObserverInstance = null;

function initShimmerObserver() {
    if (!shimmerObserverInstance) {
        shimmerObserverInstance = new ShimmerAnimationObserver();
    }
    return shimmerObserverInstance;
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initShimmerObserver);
} else {
    initShimmerObserver();
}

window.shimmerObserver = shimmerObserverInstance;
