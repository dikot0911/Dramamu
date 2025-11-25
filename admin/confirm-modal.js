/**
 * Reusable Custom Confirmation Modal for Admin Panel
 * 
 * Usage:
 * showConfirmModal({
 *   title: 'Konfirmasi Aksi',
 *   message: 'Apakah Anda yakin ingin melanjutkan?',
 *   confirmText: 'Ya, Lanjutkan',
 *   cancelText: 'Batal',
 *   type: 'danger', // 'danger', 'warning', 'info', 'success'
 *   onConfirm: async () => {
 *     // Your async action here
 *     await someAsyncOperation();
 *   },
 *   onCancel: () => {
 *     // Optional cancel callback
 *   }
 * });
 */

(function(window) {
    'use strict';

    // Modal state
    let currentModal = null;
    let isProcessing = false;

    /**
     * Show confirmation modal
     * @param {Object} config - Modal configuration
     * @param {string} config.title - Modal title
     * @param {string} config.message - Confirmation message (supports HTML)
     * @param {string} [config.confirmText='Konfirmasi'] - Confirm button text
     * @param {string} [config.cancelText='Batal'] - Cancel button text
     * @param {Function} config.onConfirm - Callback when confirmed (can be async)
     * @param {Function} [config.onCancel] - Callback when cancelled
     * @param {string} [config.type='warning'] - Modal type: 'danger', 'warning', 'info', 'success'
     * @returns {Promise<boolean>} - Resolves to true if confirmed, false if cancelled
     */
    window.showConfirmModal = function(config) {
        return new Promise((resolveModal, rejectModal) => {
            // Validate required parameters
            if (!config || !config.title || !config.message) {
                console.error('showConfirmModal: title and message are required');
                rejectModal(new Error('Invalid modal configuration'));
                return;
            }

            // Close existing modal if any
            if (currentModal) {
                closeConfirmModal();
            }

            // Default values
            const title = config.title;
            const message = config.message;
            const confirmText = config.confirmText || 'Konfirmasi';
            const cancelText = config.cancelText || 'Batal';
            const onConfirm = config.onConfirm;
            const onCancel = config.onCancel || null;
            const type = config.type || 'warning';

            // Get icon based on type
            const icon = getIconForType(type);
            
            // Get color class based on type
            const colorClass = getColorClassForType(type);
            
            // Get confirm button class based on type
            const confirmBtnClass = getConfirmButtonClass(type);

            // Create modal HTML with type-specific styling
            const modalHTML = `
                <div id="customConfirmModal" class="modal" style="z-index: 9999; display: none;">
                    <div class="modal-content" style="max-width: 500px;">
                        <div class="modal-header modal-header-${colorClass}">
                            <h2>
                                <span data-icon="${icon}" data-icon-size="sm" style="margin-right: 8px;"></span>
                                ${escapeHtml(title)}
                            </h2>
                            <button class="modal-close" id="customConfirmModalClose" aria-label="Close">&times;</button>
                        </div>
                        <div class="modal-body">
                            <p style="margin: 0; color: var(--text-secondary); font-size: var(--font-md); line-height: 1.6;">
                                ${message}
                            </p>
                        </div>
                        <div class="modal-footer">
                            <button class="btn btn-secondary" id="customConfirmModalCancel" type="button">
                                <span data-icon="x" data-icon-size="sm"></span>
                                <span>${escapeHtml(cancelText)}</span>
                            </button>
                            <button class="btn ${confirmBtnClass}" id="customConfirmModalConfirm" type="button">
                                <span data-icon="check" data-icon-size="sm"></span>
                                <span>${escapeHtml(confirmText)}</span>
                            </button>
                        </div>
                    </div>
                </div>
            `;

            // Inject modal into DOM
            const modalContainer = document.createElement('div');
            modalContainer.innerHTML = modalHTML;
            const modal = modalContainer.firstElementChild;
            document.body.appendChild(modal);

            // Store reference
            currentModal = modal;

            // Render icons if available (with fallback)
            if (typeof window.Icons?.renderIcons === 'function') {
                try {
                    window.Icons.renderIcons();
                } catch (e) {
                    console.debug('Icon rendering failed:', e);
                }
            } else {
                console.debug('Icons.renderIcons not available');
            }

            // Get elements
            const closeBtn = modal.querySelector('#customConfirmModalClose');
            const cancelBtn = modal.querySelector('#customConfirmModalCancel');
            const confirmBtn = modal.querySelector('#customConfirmModalConfirm');

            // Event handlers
            const handleClose = () => {
                if (!isProcessing) {
                    closeConfirmModal();
                    if (onCancel) {
                        try {
                            onCancel();
                        } catch (error) {
                            console.error('Cancel callback error:', error);
                        }
                    }
                    resolveModal(false);
                }
            };

            const handleConfirm = async () => {
                if (isProcessing) return;

                try {
                    isProcessing = true;
                    
                    // Show loading state
                    const originalHTML = confirmBtn.innerHTML;
                    confirmBtn.disabled = true;
                    confirmBtn.innerHTML = `
                        <span data-icon="loader" data-icon-size="sm" style="animation: spin 1s linear infinite;"></span>
                        <span>Memproses...</span>
                    `;
                    
                    // Render loading icon
                    if (typeof window.Icons?.renderIcons === 'function') {
                        try {
                            window.Icons.renderIcons();
                        } catch (e) {
                            console.debug('Icon rendering failed:', e);
                        }
                    }

                    // Execute confirm callback if provided
                    if (onConfirm && typeof onConfirm === 'function') {
                        await Promise.resolve(onConfirm());
                    }

                    // Close modal after successful confirmation
                    closeConfirmModal();
                    resolveModal(true);

                } catch (error) {
                    console.error('Confirm action error:', error);
                    
                    // Show error toast if available, with multiple fallback levels
                    const errorMsg = error.message || 'Terjadi kesalahan';
                    
                    if (typeof window.AdminPanel?.showToast === 'function') {
                        try {
                            window.AdminPanel.showToast(errorMsg, 'error');
                        } catch (toastError) {
                            console.error('Toast display error:', toastError);
                            alert(errorMsg);
                        }
                    } else if (typeof window.notification?.error === 'function') {
                        // Fallback to alternative notification system if available
                        try {
                            window.notification.error(errorMsg, 'error');
                        } catch (notifError) {
                            console.error('Notification error:', notifError);
                            alert(errorMsg);
                        }
                    } else {
                        // Final fallback to browser alert
                        console.error('Error:', errorMsg);
                        alert(errorMsg);
                    }
                    
                    // Reset button state
                    confirmBtn.disabled = false;
                    confirmBtn.innerHTML = `
                        <span data-icon="check" data-icon-size="sm"></span>
                        <span>${escapeHtml(confirmText)}</span>
                    `;
                    
                    if (typeof window.Icons?.renderIcons === 'function') {
                        try {
                            window.Icons.renderIcons();
                        } catch (e) {
                            console.debug('Icon rendering failed:', e);
                        }
                    }
                    
                    isProcessing = false;
                    
                    // Don't close modal on error, let user try again or cancel
                }
            };

            const handleBackdropClick = (e) => {
                if (e.target === modal) {
                    handleClose();
                }
            };

            const handleEscKey = (e) => {
                if (e.key === 'Escape' || e.key === 'Esc') {
                    handleClose();
                }
            };

            // Attach event listeners
            closeBtn.addEventListener('click', handleClose);
            cancelBtn.addEventListener('click', handleClose);
            confirmBtn.addEventListener('click', handleConfirm);
            modal.addEventListener('click', handleBackdropClick);
            document.addEventListener('keydown', handleEscKey);

            // Store event listeners for cleanup
            modal._eventListeners = {
                handleClose,
                handleConfirm,
                handleBackdropClick,
                handleEscKey
            };

            // Show modal with fade-in animation
            requestAnimationFrame(() => {
                modal.style.display = 'flex';
                requestAnimationFrame(() => {
                    modal.classList.add('show');
                });
            });
        });
    };

    /**
     * Close and cleanup the confirmation modal
     */
    function closeConfirmModal() {
        if (!currentModal) return;

        const modal = currentModal;
        
        // Fade out animation
        modal.classList.remove('show');

        // Wait for animation to complete
        setTimeout(() => {
            // Remove event listeners
            if (modal._eventListeners) {
                const { handleClose, handleConfirm, handleBackdropClick, handleEscKey } = modal._eventListeners;
                
                const closeBtn = modal.querySelector('#customConfirmModalClose');
                const cancelBtn = modal.querySelector('#customConfirmModalCancel');
                const confirmBtn = modal.querySelector('#customConfirmModalConfirm');

                if (closeBtn) closeBtn.removeEventListener('click', handleClose);
                if (cancelBtn) cancelBtn.removeEventListener('click', handleClose);
                if (confirmBtn) confirmBtn.removeEventListener('click', handleConfirm);
                modal.removeEventListener('click', handleBackdropClick);
                document.removeEventListener('keydown', handleEscKey);
            }

            // Remove from DOM
            if (modal.parentNode) {
                modal.parentNode.removeChild(modal);
            }

            currentModal = null;
            isProcessing = false;
        }, 300); // Match CSS transition duration
    }

    /**
     * Get icon name based on modal type
     */
    function getIconForType(type) {
        const iconMap = {
            'danger': 'alertTriangle',
            'warning': 'alertCircle',
            'info': 'info',
            'success': 'checkCircle'
        };
        return iconMap[type] || 'alertCircle';
    }

    /**
     * Get color class based on modal type
     */
    function getColorClassForType(type) {
        const colorMap = {
            'danger': 'danger',
            'warning': 'warning',
            'info': 'info',
            'success': 'success'
        };
        return colorMap[type] || 'warning';
    }

    /**
     * Get confirm button class based on modal type
     */
    function getConfirmButtonClass(type) {
        const buttonClassMap = {
            'danger': 'btn-danger',
            'warning': 'btn-warning',
            'info': 'btn-info',
            'success': 'btn-success'
        };
        return buttonClassMap[type] || 'btn-primary';
    }

    /**
     * Escape HTML to prevent XSS
     */
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // Add CSS for modal animations and type-specific styling if not already present
    if (!document.getElementById('confirmModalStyles')) {
        const style = document.createElement('style');
        style.id = 'confirmModalStyles';
        style.textContent = `
            #customConfirmModal {
                opacity: 0;
                transition: opacity 0.3s ease;
            }
            
            #customConfirmModal.show {
                opacity: 1;
            }
            
            #customConfirmModal .modal-content {
                transform: scale(0.7);
                transition: transform 0.3s ease;
            }
            
            #customConfirmModal.show .modal-content {
                transform: scale(1);
            }
            
            /* Type-specific header styling */
            .modal-header-danger {
                border-bottom: 2px solid #dc2626;
            }
            
            .modal-header-danger h2 {
                color: #dc2626;
            }
            
            .modal-header-warning {
                border-bottom: 2px solid #f59e0b;
            }
            
            .modal-header-warning h2 {
                color: #f59e0b;
            }
            
            .modal-header-info {
                border-bottom: 2px solid #3b82f6;
            }
            
            .modal-header-info h2 {
                color: #3b82f6;
            }
            
            .modal-header-success {
                border-bottom: 2px solid #10b981;
            }
            
            .modal-header-success h2 {
                color: #10b981;
            }
            
            /* Type-specific button colors */
            .btn-warning {
                background-color: #f59e0b;
                color: white;
            }
            
            .btn-warning:hover {
                background-color: #d97706;
            }
            
            .btn-info {
                background-color: #3b82f6;
                color: white;
            }
            
            .btn-info:hover {
                background-color: #2563eb;
            }
            
            .btn-success {
                background-color: #10b981;
                color: white;
            }
            
            .btn-success:hover {
                background-color: #059669;
            }
            
            @keyframes spin {
                from {
                    transform: rotate(0deg);
                }
                to {
                    transform: rotate(360deg);
                }
            }
        `;
        document.head.appendChild(style);
    }

})(window);
