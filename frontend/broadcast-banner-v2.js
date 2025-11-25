// Broadcast Banner V2 - Mini App Only (Golden, Simple, 1-line)
// Shows active broadcasts with collapse/expand for long text

class BroadcastBannerV2 {
    constructor() {
        this.broadcasts = [];
        this.dismissedIds = new Set();
        this.expandedIds = new Set();
        this.loadDismissed();
    }

    loadDismissed() {
        const dismissed = localStorage.getItem('broadcast_v2_dismissed');
        if (dismissed) {
            this.dismissedIds = new Set(JSON.parse(dismissed));
        }
    }

    saveDismissed() {
        localStorage.setItem('broadcast_v2_dismissed', JSON.stringify(Array.from(this.dismissedIds)));
    }

    async load() {
        try {
            const response = await fetch(`${window.API_BASE_URL || ''}/api/broadcasts-v2/active`);
            if (!response.ok) return;
            const data = await response.json();
            this.broadcasts = data.broadcasts || [];
            this.render();
        } catch (error) {
            console.error('Error loading broadcasts v2:', error);
        }
    }

    render() {
        const container = document.getElementById('broadcast-banner-v2');
        if (!container) return;

        const activeBroadcasts = this.broadcasts.filter(b => !this.dismissedIds.has(b.id));
        
        if (activeBroadcasts.length === 0) {
            container.innerHTML = '';
            return;
        }

        container.innerHTML = activeBroadcasts.map(broadcast => `
            <div class="broadcast-v2-item" id="broadcast-${broadcast.id}">
                <div class="broadcast-v2-content">
                    <div class="broadcast-v2-text" id="text-${broadcast.id}">
                        <span class="broadcast-v2-msg">${this.escapeHtml(broadcast.message)}</span>
                        ${broadcast.message.length > 60 ? `
                            <button class="broadcast-v2-toggle" onclick="window.broadcastBannerV2.toggleExpand(${broadcast.id})" title="Expand/Collapse">
                                <span id="toggle-${broadcast.id}">v</span>
                            </button>
                        ` : ''}
                    </div>
                </div>
                <button class="broadcast-v2-dismiss" onclick="window.broadcastBannerV2.dismiss(${broadcast.id})" title="Dismiss">×</button>
            </div>
        `).join('');
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    toggleExpand(broadcastId) {
        if (this.expandedIds.has(broadcastId)) {
            this.expandedIds.delete(broadcastId);
        } else {
            this.expandedIds.add(broadcastId);
        }
        
        const textEl = document.getElementById(`text-${broadcastId}`);
        const toggleEl = document.getElementById(`toggle-${broadcastId}`);
        if (textEl) {
            textEl.classList.toggle('expanded');
            if (toggleEl) toggleEl.textContent = this.expandedIds.has(broadcastId) ? '^' : 'v';
        }
    }

    dismiss(broadcastId) {
        this.dismissedIds.add(broadcastId);
        this.saveDismissed();
        const el = document.getElementById(`broadcast-${broadcastId}`);
        if (el) el.remove();
    }
}

// Initialize on page load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.broadcastBannerV2 = new BroadcastBannerV2();
        window.broadcastBannerV2.load();
    });
} else {
    window.broadcastBannerV2 = new BroadcastBannerV2();
    window.broadcastBannerV2.load();
}
