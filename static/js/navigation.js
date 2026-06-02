// navigation.js - Dynamic Permission-Based Navigation
class DynamicNavigationManager {
    constructor() {
        this.initialized = false;
        this.userPermissions = {};
        this.init();
    }

    async init() {
        if (this.initialized) return;
        
        try {
            // Hide everything initially to prevent flashing - FIXED VERSION
            this.hideAllRestrictedContent();
            
            await this.setupDynamicNavigation();
            this.initialized = true;
        } catch (error) {
            console.log('Navigation setup skipped:', error);
        }
    }

    // FIXED: Hide restricted content WITHOUT breaking dropdowns
    hideAllRestrictedContent() {
        // Define all potentially restricted pages
        const restrictedPages = ['/configurations', '/users', '/admin-dashboard'];
        
        restrictedPages.forEach(page => {
            const links = document.querySelectorAll(`a[href="${page}"]`);
            links.forEach(link => {
                // CRITICAL: Never hide dropdown parents or dropdown content
                const isDropdownItem = link.parentElement.classList.contains('dropdown') || 
                                      link.parentElement.parentElement.classList.contains('dropdown-content');
                
                if (link.parentElement && !isDropdownItem) {
                    link.parentElement.classList.add('nav-hidden');
                    link.parentElement.style.display = 'none';
                }
            });
        });
        
        // Hide quick actions initially
        const quickActions = document.querySelector('.quick-actions');
        const quickActionsHeading = document.querySelector('.quick-actions-heading');
        if (quickActions) quickActions.style.display = 'none';
        if (quickActionsHeading) quickActionsHeading.style.display = 'none';
    }

    async setupDynamicNavigation() {
        try {
            // Check authentication and get user data
            const [adminResponse, userResponse] = await Promise.all([
                fetch('/admin/check-auth'),
                fetch('/user/check-auth')
            ]);
            
            const adminAuth = await adminResponse.json();
            const userAuth = await userResponse.json();

            console.log('🔍 DEBUG - Admin auth response:', adminAuth);
            console.log('🔍 DEBUG - User auth response:', userAuth);

            // FIXED: Allow access to login pages without redirect
            const currentPath = window.location.pathname;
            const allowedPathsWithoutAuth = [
                '/gateway', 
                '/user-login', 
                '/admin',
                '/admin-login',
                '/admin/login'
            ];

            console.log('🔍 DEBUG - Current path:', currentPath);
            console.log('🔍 DEBUG - Allowed paths:', allowedPathsWithoutAuth);

            // If on login pages, don't redirect to gateway
            if (allowedPathsWithoutAuth.includes(currentPath)) {
                console.log('✅ Allowed path without authentication:', currentPath);
                return;
            }

            // Redirect if not authenticated and not on allowed paths
            if (!adminAuth.authenticated && !userAuth.authenticated) {
                console.log('❌ Not authenticated, redirecting to gateway');
                window.location.href = '/gateway';
                return;
            }

            // Get user data and permissions
            const userData = adminAuth.authenticated ? adminAuth : userAuth;
            const userType = adminAuth.authenticated ? 'admin' : 'user';
            this.userPermissions = userData.permissions || await this.getDynamicPermissions(userType);
            
            // Apply dynamic permissions
            this.applyDynamicNavigationPermissions();
            this.applyDynamicQuickActionsPermissions();
            this.updateUserDisplay(userData, userType);
            
        } catch (error) {
            console.error('Navigation setup error:', error);
        }
    }

    async getDynamicPermissions(userType) {
        try {
            console.log('🔄 Navigation: Fetching permissions for:', userType);
            
            const response = await fetch(`/api/user/permissions?user_type=${userType}`);
            const data = await response.json();
            
            if (data.success && data.permissions) {
                console.log('✅ Navigation: Permissions received');
                return data.permissions;
            }
            
            throw new Error('No permissions data');
            
        } catch (error) {
            console.log('❌ Navigation: Using basic permissions');
            return this.getBasicRolePermissions(userType);
        }
    }

    getBasicRolePermissions(userType) {
        // Basic role-based permissions as last resort
        if (userType === 'admin') {
            return {
                can_view_quick_actions: true,
                allowed_pages: ['/', '/dashboard', '/configurations', '/dataimport', '/inventory', '/customers', '/orders', '/admin-dashboard', '/user-registration', '/admin-registration'],
                allowed_quick_actions: ['create_order', 'manage_inventory', 'add_user', 'view_reports']
            };
        } else {
            return {
                can_view_quick_actions: true,
                allowed_pages: ['/', '/dashboard', '/inventory', '/orders'],
                allowed_quick_actions: ['create_order', 'manage_inventory', 'view_reports']
            };
        }
    }

    // FIXED: Apply navigation permissions without breaking dropdown structure
    applyDynamicNavigationPermissions() {
        const allNavLinks = document.querySelectorAll('ul li:not(.dropdown) a'); // Only target non-dropdown links
        
        allNavLinks.forEach(link => {
            const href = link.getAttribute('href');
            if (href && href !== '#' && !href.includes('logout')) {
                const isAllowed = this.userPermissions.allowed_pages.includes(href);
                
                if (isAllowed) {
                    link.parentElement.style.display = 'block';
                    link.parentElement.classList.remove('nav-hidden');
                } else {
                    link.parentElement.style.display = 'none';
                    link.parentElement.classList.add('nav-hidden');
                }
            }
        });

        // Special handling for dropdown items - never hide the dropdown container
        const dropdownContainers = document.querySelectorAll('li.dropdown');
        dropdownContainers.forEach(dropdown => {
            dropdown.style.display = 'block';
            dropdown.classList.remove('nav-hidden');
        });
    }

    applyDynamicQuickActionsPermissions() {
        const quickActionsSection = document.querySelector('.quick-actions');
        const quickActionsHeading = document.querySelector('.quick-actions-heading');
        
        // Check global quick actions permission
        if (!this.userPermissions.can_view_quick_actions) {
            if (quickActionsSection) quickActionsSection.style.display = 'none';
            if (quickActionsHeading) quickActionsHeading.style.display = 'none';
            return;
        }
        
        // Show quick actions section
        if (quickActionsSection) quickActionsSection.style.display = 'grid';
        if (quickActionsHeading) quickActionsHeading.style.display = 'block';
        
        // Apply individual action permissions
        this.applyIndividualActionPermissions();
    }

    applyIndividualActionPermissions() {
        const quickActionLinks = document.querySelectorAll('.quick-actions .action-btn');
        let visibleActionCount = 0;
        
        quickActionLinks.forEach(action => {
            const permissionKey = action.getAttribute('data-permission');
            const isAllowed = this.userPermissions.allowed_quick_actions.includes(permissionKey);
            
            if (isAllowed) {
                action.style.display = 'flex';
                visibleActionCount++;
            } else {
                action.style.display = 'none';
            }
        });
        
        // Hide entire section if no actions are visible
        if (visibleActionCount === 0) {
            const quickActionsSection = document.querySelector('.quick-actions');
            const quickActionsHeading = document.querySelector('.quick-actions-heading');
            
            if (quickActionsSection) quickActionsSection.style.display = 'none';
            if (quickActionsHeading) quickActionsHeading.style.display = 'none';
        }
    }

    updateUserDisplay(userData, userType) {
        const logoutButtons = document.querySelectorAll('a[onclick*="logout"], button[onclick*="logout"]');
        
        console.log('🔍 DEBUG navigation.js - userData:', userData);
        
        logoutButtons.forEach(btn => {
            const actualUserName = userData.user_name || 
                                 userData.user ||
                                 userData.customer_name || 
                                 userData.username ||
                                 userData.name ||
                                 userData.email ||
                                 'User';
            
            console.log('🔍 DEBUG - Final username in nav:', actualUserName);
            
            const icon = userType === 'admin' ? 'fa-crown' : 'fa-user';
            btn.innerHTML = `<i class="fas ${icon}"></i> ${actualUserName} | Logout`;
        });
    }

    // Method to refresh permissions (can be called if permissions change)
    async refreshPermissions() {
        this.userPermissions = {};
        await this.setupDynamicNavigation();
    }

    // Method to check if user has specific permission
    hasPermission(permissionKey) {
        return this.userPermissions.allowed_quick_actions.includes(permissionKey);
    }

    // Method to check if user can access specific page
    canAccessPage(pagePath) {
        return this.userPermissions.allowed_pages.includes(pagePath);
    }
}

// Global navigation instance
window.navigationManager = new DynamicNavigationManager();

// Safe initialization
document.addEventListener('DOMContentLoaded', () => {
    console.log('Dynamic Navigation Manager initialized');
});

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = DynamicNavigationManager;
}