/**
 * Notification System - Reusable notification handler
 * Features: Toast notification, sound, vibration (iPhone-style)
 * Manual dismiss for payment pending/success, auto-dismiss for others
 * With comprehensive debugging
 */

class NotificationSystem {
    constructor() {
        this.notificationTimeout = null;
        this.notificationElement = null;
        this.globalAudioContext = null;
        console.log('📢 Notification System: Initializing...');
        this.initNotificationElement();
    }

    initNotificationElement() {
        // Check if notification element already exists
        this.notificationElement = document.getElementById('notificationContainer');
        
        if (!this.notificationElement) {
            console.log('📢 Notification System: Creating DOM elements...');
            // Create notification element if it doesn't exist
            this.notificationElement = document.createElement('div');
            this.notificationElement.id = 'notificationContainer';
            this.notificationElement.innerHTML = `
                <div class="notification-toast" id="notificationToast">
                    <div class="notification-icon" id="notificationIcon"></div>
                    <div class="notification-message" id="notificationMessage"></div>
                    <div class="notification-close-hint" id="notificationCloseHint"></div>
                </div>
            `;
            document.body.appendChild(this.notificationElement);
            
            // Add click-to-close handler
            const toastElement = document.getElementById('notificationToast');
            toastElement.addEventListener('click', () => {
                console.log('📢 Notification clicked - dismissing');
                this.close();
            });
            
            // Add CSS if not already present
            if (!document.getElementById('notificationStyles')) {
                const styleElement = document.createElement('style');
                styleElement.id = 'notificationStyles';
                styleElement.textContent = `
                    #notificationContainer {
                        position: fixed;
                        bottom: 100px;
                        left: 50%;
                        z-index: 99999;
                    }

                    .notification-toast {
                        transform: translateX(-50%) translateY(8px);
                        background: rgba(30, 41, 59, 0.98);
                        border: 2px solid rgba(212, 175, 55, 0.4);
                        border-radius: 12px;
                        padding: 16px 20px;
                        max-width: 280px;
                        width: auto;
                        text-align: center;
                        opacity: 0;
                        visibility: hidden;
                        transition: opacity 0.2s ease, transform 0.2s ease, visibility 0.2s ease;
                        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
                    }

                    .notification-toast.show {
                        opacity: 1;
                        visibility: visible;
                        transform: translateX(-50%) translateY(0);
                    }

                    .notification-icon {
                        width: 32px;
                        height: 32px;
                        margin: 0 auto 8px;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                    }

                    .notification-icon svg {
                        width: 100%;
                        height: 100%;
                    }

                    .notification-message {
                        font-size: 14px;
                        color: #E5E7EB;
                        line-height: 1.4;
                        font-family: 'Manrope', sans-serif;
                        margin-bottom: 6px;
                    }

                    .notification-close-hint {
                        font-size: 11px;
                        color: rgba(255, 255, 255, 0.5);
                        font-style: italic;
                    }

                    .notification-toast.success {
                        border-color: rgba(34, 197, 94, 0.3);
                    }

                    .notification-toast.error {
                        border-color: rgba(239, 68, 68, 0.3);
                    }

                    .notification-toast.info {
                        border-color: rgba(59, 130, 246, 0.3);
                    }

                    .notification-toast.pending {
                        border-color: rgba(168, 85, 247, 0.3);
                    }

                    .notification-toast {
                        cursor: pointer;
                    }
                `;
                document.head.appendChild(styleElement);
            }
            console.log('📢 Notification System: DOM elements created ✓');
        }
    }

    /**
     * Get or create audio context (singleton pattern)
     */
    getAudioContext() {
        if (!this.globalAudioContext) {
            try {
                this.globalAudioContext = new (window.AudioContext || window.webkitAudioContext)();
                console.log('🔊 Audio Context: Created successfully');
            } catch (e) {
                console.warn('🔊 Audio Context: Not supported -', e.message);
            }
        }
        return this.globalAudioContext;
    }

    /**
     * Close notification
     */
    close() {
        const toast = this.notificationElement.querySelector('.notification-toast');
        if (toast) {
            toast.classList.remove('show');
        }
        if (this.notificationTimeout) {
            clearTimeout(this.notificationTimeout);
        }
    }

    /**
     * Show notification
     * @param {string} message - Notification message
     * @param {string} type - Type: 'success', 'error', 'info', 'pending'
     * @param {Object} options - Additional options
     *   - duration: milliseconds (auto-dismiss) - not used for pending/success
     *   - sound: boolean (default: true except drama.html)
     *   - vibrate: boolean (default: true except drama.html)
     *   - soundType: 'payment' (backward compatibility)
     */
    show(message, type = 'success', options = {}) {
        const {
            duration = 3000,
            sound = true,
            vibrate = true,
            soundType = null  // backward compatibility
        } = options;

        console.log(`📢 Notification: Showing [${type}] - sound: ${sound}, vibrate: ${vibrate}`);

        // Clear existing timeout
        if (this.notificationTimeout) {
            clearTimeout(this.notificationTimeout);
        }

        const toast = this.notificationElement.querySelector('.notification-toast');
        const iconElement = document.getElementById('notificationIcon');
        const messageElement = document.getElementById('notificationMessage');
        const closeHintElement = document.getElementById('notificationCloseHint');

        // Remove all type classes
        toast.classList.remove('success', 'error', 'info', 'pending', 'show');

        // Set icon
        const icons = {
            success: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`,
            error: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`,
            info: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`,
            pending: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="1"/><circle cx="12" cy="5" r="1"/><circle cx="12" cy="19" r="1"/></svg>`
        };

        iconElement.innerHTML = icons[type] || icons['success'];
        iconElement.style.color = type === 'success' ? '#22c55e' : type === 'error' ? '#ef4444' : type === 'pending' ? '#a855f7' : '#3b82f6';
        
        messageElement.textContent = message;

        // Show close hint only for pending and success
        if (type === 'pending' || type === 'success') {
            closeHintElement.textContent = 'Klik untuk tutup';
        } else {
            closeHintElement.textContent = '';
        }

        // Add type class
        toast.classList.add(type);

        // Show notification
        setTimeout(() => {
            toast.classList.add('show');
        }, 10);

        // Play sound if requested
        if (sound) {
            // Use soundType if provided (backward compatibility), otherwise use type
            const effectType = soundType || type;
            this.playSound(effectType);
        }

        // Vibrate if requested
        if (vibrate) {
            this.vibrate(type);
        }

        // Auto dismiss only for error and info (not for pending/success)
        if (type !== 'pending' && type !== 'success') {
            this.notificationTimeout = setTimeout(() => {
                toast.classList.remove('show');
            }, duration);
        }

        return this;
    }

    /**
     * Play notification sound
     * @param {string} soundType - 'payment', 'success', 'error', 'pending', 'info'
     */
    playSound(soundType = 'success') {
        try {
            const ctx = this.getAudioContext();
            if (!ctx) {
                console.warn('🔊 Audio Context: Not available, skipping sound');
                return;
            }

            console.log(`🔊 Playing sound: ${soundType}`);

            // Resume context if suspended
            if (ctx.state === 'suspended') {
                console.log('🔊 Audio Context: Was suspended, resuming...');
                ctx.resume().then(() => {
                    console.log('🔊 Audio Context: Resumed successfully');
                    this._playSoundEffect(ctx, soundType);
                }).catch(e => {
                    console.warn('🔊 Audio Context: Failed to resume -', e.message);
                });
            } else {
                this._playSoundEffect(ctx, soundType);
            }
        } catch (e) {
            console.warn('🔊 Audio error:', e.message);
        }
    }

    /**
     * Internal sound effect player
     */
    _playSoundEffect(ctx, soundType) {
        switch (soundType) {
            case 'pending':
                this.playPendingSound(ctx);
                break;
            case 'success':
                this.playSuccessSound(ctx);
                break;
            case 'error':
                this.playErrorSound(ctx);
                break;
            case 'info':
                this.playInfoSound(ctx);
                break;
            case 'payment':
                this.playPaymentSound(ctx);
                break;
            default:
                this.playSuccessSound(ctx);
        }
    }

    /**
     * Payment sound - Two ascending beeps
     */
    playPaymentSound(ctx) {
        try {
            const now = ctx.currentTime;
            const gainNode = ctx.createGain();
            gainNode.connect(ctx.destination);

            // First beep
            const osc1 = ctx.createOscillator();
            osc1.frequency.value = 880;
            osc1.type = 'sine';
            osc1.connect(gainNode);
            gainNode.gain.setValueAtTime(1.0, now);
            osc1.start(now);
            osc1.stop(now + 0.12);

            // Second beep
            const osc2 = ctx.createOscillator();
            osc2.frequency.value = 1100;
            osc2.type = 'sine';
            osc2.connect(gainNode);
            gainNode.gain.setValueAtTime(1.0, now + 0.14);
            osc2.start(now + 0.14);
            osc2.stop(now + 0.26);

            // Fade out
            gainNode.gain.exponentialRampToValueAtTime(0.01, now + 0.35);
            console.log('🔊 Payment sound played ✓');
        } catch (e) {
            console.warn('🔊 Payment sound error:', e.message);
        }
    }

    /**
     * Pending sound - Double beep pattern
     */
    playPendingSound(ctx) {
        try {
            const now = ctx.currentTime;
            const gainNode = ctx.createGain();
            gainNode.connect(ctx.destination);

            // First beep
            const osc1 = ctx.createOscillator();
            osc1.frequency.value = 600;
            osc1.type = 'sine';
            osc1.connect(gainNode);
            gainNode.gain.setValueAtTime(1.0, now);
            osc1.start(now);
            osc1.stop(now + 0.15);

            // Second beep
            const osc2 = ctx.createOscillator();
            osc2.frequency.value = 600;
            osc2.type = 'sine';
            osc2.connect(gainNode);
            gainNode.gain.setValueAtTime(1.0, now + 0.2);
            osc2.start(now + 0.2);
            osc2.stop(now + 0.35);

            // Fade out
            gainNode.gain.exponentialRampToValueAtTime(0.01, now + 0.4);
            console.log('🔊 Pending sound played ✓');
        } catch (e) {
            console.warn('🔊 Pending sound error:', e.message);
        }
    }

    /**
     * Success sound - Two ascending beeps
     */
    playSuccessSound(ctx) {
        try {
            const now = ctx.currentTime;
            const gainNode = ctx.createGain();
            gainNode.connect(ctx.destination);

            // First beep - lower frequency
            const osc1 = ctx.createOscillator();
            osc1.frequency.value = 800;
            osc1.type = 'sine';
            osc1.connect(gainNode);
            gainNode.gain.setValueAtTime(1.0, now);
            osc1.start(now);
            osc1.stop(now + 0.12);

            // Second beep - higher frequency
            const osc2 = ctx.createOscillator();
            osc2.frequency.value = 1000;
            osc2.type = 'sine';
            osc2.connect(gainNode);
            gainNode.gain.setValueAtTime(1.0, now + 0.14);
            osc2.start(now + 0.14);
            osc2.stop(now + 0.26);

            // Fade out
            gainNode.gain.exponentialRampToValueAtTime(0.01, now + 0.35);
            console.log('🔊 Success sound played ✓');
        } catch (e) {
            console.warn('🔊 Success sound error:', e.message);
        }
    }

    /**
     * Error sound - Descending beep
     */
    playErrorSound(ctx) {
        try {
            const now = ctx.currentTime;
            const gainNode = ctx.createGain();
            gainNode.connect(ctx.destination);

            const osc = ctx.createOscillator();
            osc.frequency.setValueAtTime(800, now);
            osc.frequency.exponentialRampToValueAtTime(400, now + 0.2);
            osc.type = 'sine';
            osc.connect(gainNode);

            gainNode.gain.setValueAtTime(1.0, now);
            gainNode.gain.exponentialRampToValueAtTime(0.01, now + 0.25);

            osc.start(now);
            osc.stop(now + 0.25);
            console.log('🔊 Error sound played ✓');
        } catch (e) {
            console.warn('🔊 Error sound error:', e.message);
        }
    }

    /**
     * Info sound - Rising tone
     */
    playInfoSound(ctx) {
        try {
            const now = ctx.currentTime;
            const gainNode = ctx.createGain();
            gainNode.connect(ctx.destination);

            const osc = ctx.createOscillator();
            osc.frequency.setValueAtTime(500, now);
            osc.frequency.exponentialRampToValueAtTime(900, now + 0.2);
            osc.type = 'sine';
            osc.connect(gainNode);

            gainNode.gain.setValueAtTime(1.0, now);
            gainNode.gain.exponentialRampToValueAtTime(0.01, now + 0.2);

            osc.start(now);
            osc.stop(now + 0.2);
            console.log('🔊 Info sound played ✓');
        } catch (e) {
            console.warn('🔊 Info sound error:', e.message);
        }
    }

    /**
     * Vibrate device with pattern based on notification type
     * @param {string} type - 'pending', 'success', 'error', 'info'
     */
    vibrate(type = 'success') {
        console.log(`📳 Attempting vibration: ${type}`);
        
        if ('vibrate' in navigator || window.Telegram?.WebApp?.HapticFeedback) {
            try {
                // Try Telegram haptic feedback first (iPhone-style)
                if (window.Telegram?.WebApp?.HapticFeedback) {
                    console.log('📳 Using Telegram HapticFeedback');
                    switch (type) {
                        case 'pending':
                            window.Telegram.WebApp.HapticFeedback.impactOccurred('medium');
                            console.log('📳 Haptic: medium ✓');
                            break;
                        case 'success':
                            window.Telegram.WebApp.HapticFeedback.impactOccurred('heavy');
                            console.log('📳 Haptic: heavy ✓');
                            break;
                        case 'error':
                            window.Telegram.WebApp.HapticFeedback.impactOccurred('heavy');
                            console.log('📳 Haptic: heavy ✓');
                            break;
                        case 'info':
                            window.Telegram.WebApp.HapticFeedback.impactOccurred('light');
                            console.log('📳 Haptic: light ✓');
                            break;
                    }
                } else if (navigator.vibrate) {
                    console.log('📳 Using navigator.vibrate');
                    // Fallback to vibration API
                    let pattern;
                    switch (type) {
                        case 'pending':
                            pattern = [50, 30, 50]; // Medium
                            break;
                        case 'success':
                            pattern = [100, 50, 100]; // Heavy
                            break;
                        case 'error':
                            pattern = [80, 20, 80, 20, 80]; // Rapid
                            break;
                        case 'info':
                            pattern = [30, 30, 30]; // Light
                            break;
                        default:
                            pattern = [50, 30, 50];
                    }
                    navigator.vibrate(pattern);
                    console.log(`📳 Vibration pattern applied: ${pattern} ✓`);
                }
            } catch (e) {
                console.warn('📳 Vibration error:', e.message);
            }
        } else {
            console.warn('📳 Vibration API not available');
        }
    }

    /**
     * Success notification
     */
    success(message, options = {}) {
        return this.show(message, 'success', { ...options, sound: true, vibrate: true });
    }

    /**
     * Error notification
     */
    error(message, options = {}) {
        return this.show(message, 'error', { ...options, sound: true, vibrate: true });
    }

    /**
     * Info notification
     */
    info(message, options = {}) {
        return this.show(message, 'info', { ...options, sound: true, vibrate: true });
    }

    /**
     * Pending notification (manual dismiss required)
     */
    pending(message, options = {}) {
        return this.show(message, 'pending', { ...options, sound: true, vibrate: true });
    }
}

// Initialize global notification system
console.log('📢 Initializing Notification System...');
const notification = new NotificationSystem();
console.log('📢 Notification System initialized ✓');
