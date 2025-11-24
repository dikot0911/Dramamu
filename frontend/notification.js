/**
 * Notification System - Reusable notification handler
 * Features: Toast notification, sound, vibration
 */

class NotificationSystem {
    constructor() {
        this.notificationTimeout = null;
        this.notificationElement = null;
        this.initNotificationElement();
    }

    initNotificationElement() {
        // Check if notification element already exists
        this.notificationElement = document.getElementById('notificationContainer');
        
        if (!this.notificationElement) {
            // Create notification element if it doesn't exist
            this.notificationElement = document.createElement('div');
            this.notificationElement.id = 'notificationContainer';
            this.notificationElement.innerHTML = `
                <div class="notification-toast" id="notificationToast">
                    <div class="notification-icon" id="notificationIcon"></div>
                    <div class="notification-message" id="notificationMessage"></div>
                </div>
            `;
            document.body.appendChild(this.notificationElement);
            
            // Add click-to-close handler
            const toastElement = document.getElementById('notificationToast');
            toastElement.addEventListener('click', () => {
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

                    .notification-toast {
                        cursor: pointer;
                    }
                `;
                document.head.appendChild(styleElement);
            }
        }
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
     * @param {string} type - Type: 'success', 'error', 'info'
     * @param {Object} options - Additional options
     *   - duration: milliseconds (default: 3000)
     *   - sound: boolean (default: false)
     *   - vibrate: boolean (default: false)
     *   - soundType: 'payment', 'success', 'error' (default: 'success')
     */
    show(message, type = 'success', options = {}) {
        const {
            duration = 3000,
            sound = false,
            vibrate = false,
            soundType = 'success'
        } = options;

        // Clear existing timeout
        if (this.notificationTimeout) {
            clearTimeout(this.notificationTimeout);
        }

        const toast = this.notificationElement.querySelector('.notification-toast');
        const iconElement = document.getElementById('notificationIcon');
        const messageElement = document.getElementById('notificationMessage');

        // Remove all type classes
        toast.classList.remove('success', 'error', 'info', 'show');

        // Set icon
        const icons = {
            success: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`,
            error: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`,
            info: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`
        };

        iconElement.innerHTML = icons[type] || icons['success'];
        iconElement.style.color = type === 'success' ? '#22c55e' : type === 'error' ? '#ef4444' : '#3b82f6';
        
        messageElement.textContent = message;

        // Add type class
        toast.classList.add(type);

        // Show notification
        setTimeout(() => {
            toast.classList.add('show');
        }, 10);

        // Play sound if requested
        if (sound) {
            this.playSound(soundType);
        }

        // Vibrate if requested
        if (vibrate) {
            this.vibrate();
        }

        // Auto hide
        this.notificationTimeout = setTimeout(() => {
            toast.classList.remove('show');
        }, duration);

        return this;
    }

    /**
     * Play notification sound
     * @param {string} soundType - 'payment', 'success', 'error'
     */
    playSound(soundType = 'success') {
        try {
            // Try using Web Audio API for better compatibility
            const audioContext = new (window.AudioContext || window.webkitAudioContext)();
            
            if (soundType === 'payment') {
                // iPhone payment success sound
                this.playPaymentSound(audioContext);
            } else if (soundType === 'error') {
                // Error sound
                this.playErrorSound(audioContext);
            } else {
                // Default success sound
                this.playSuccessSound(audioContext);
            }
        } catch (e) {
            console.warn('Audio context not available:', e);
            // Fallback: try using Audio element if available
            this.playAudioFallback(soundType);
        }
    }

    /**
     * iPhone-like payment success sound using Web Audio API
     */
    playPaymentSound(audioContext) {
        try {
            const now = audioContext.currentTime;
            const gainNode = audioContext.createGain();
            gainNode.connect(audioContext.destination);

            // First beep: higher frequency
            const osc1 = audioContext.createOscillator();
            osc1.frequency.value = 880; // A5
            osc1.type = 'sine';
            osc1.connect(gainNode);
            gainNode.gain.setValueAtTime(0.3, now);
            osc1.start(now);
            osc1.stop(now + 0.12);

            // Second beep: even higher
            const osc2 = audioContext.createOscillator();
            osc2.frequency.value = 1100; // ~C#6
            osc2.type = 'sine';
            osc2.connect(gainNode);
            osc2.start(now + 0.14);
            osc2.stop(now + 0.28);

            // Fade out
            gainNode.gain.setValueAtTime(0.3, now + 0.28);
            gainNode.gain.exponentialRampToValueAtTime(0.01, now + 0.5);
        } catch (e) {
            console.warn('Payment sound error:', e);
        }
    }

    /**
     * Success sound
     */
    playSuccessSound(audioContext) {
        try {
            const now = audioContext.currentTime;
            const gainNode = audioContext.createGain();
            gainNode.connect(audioContext.destination);

            const osc = audioContext.createOscillator();
            osc.frequency.value = 800;
            osc.type = 'sine';
            osc.connect(gainNode);

            gainNode.gain.setValueAtTime(0.3, now);
            gainNode.gain.exponentialRampToValueAtTime(0.01, now + 0.2);

            osc.start(now);
            osc.stop(now + 0.2);
        } catch (e) {
            console.warn('Success sound error:', e);
        }
    }

    /**
     * Error sound
     */
    playErrorSound(audioContext) {
        try {
            const now = audioContext.currentTime;
            const gainNode = audioContext.createGain();
            gainNode.connect(audioContext.destination);

            const osc = audioContext.createOscillator();
            osc.frequency.value = 400;
            osc.type = 'sine';
            osc.connect(gainNode);

            gainNode.gain.setValueAtTime(0.3, now);
            gainNode.gain.exponentialRampToValueAtTime(0.01, now + 0.3);

            osc.start(now);
            osc.stop(now + 0.3);
        } catch (e) {
            console.warn('Error sound error:', e);
        }
    }

    /**
     * Fallback audio method
     */
    playAudioFallback(soundType) {
        try {
            // This is a fallback - in production you might use actual audio files
            console.log(`Sound played: ${soundType}`);
        } catch (e) {
            console.warn('Audio fallback error:', e);
        }
    }

    /**
     * Vibrate device (if supported)
     * @param {array|number} pattern - Vibration pattern [on, off, on...]
     */
    vibrate(pattern = [50, 30, 50]) {
        if ('vibrate' in navigator) {
            try {
                navigator.vibrate(pattern);
            } catch (e) {
                console.warn('Vibration not supported:', e);
            }
        }
    }

    /**
     * Success notification with all effects
     */
    success(message, options = {}) {
        return this.show(message, 'success', options);
    }

    /**
     * Error notification
     */
    error(message, options = {}) {
        return this.show(message, 'error', options);
    }

    /**
     * Info notification
     */
    info(message, options = {}) {
        return this.show(message, 'info', options);
    }
}

// Initialize global notification system
const notification = new NotificationSystem();
