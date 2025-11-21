// Shared Navbar Component
function createNavbar(activePage = 'home') {
    const navbarHTML = `
    <nav class="absolute bottom-0 left-0 w-full h-20 z-30" style="background: rgba(15, 15, 30, 0.98); border-top: 1px solid rgba(212, 175, 55, 0.2);">
        <div class="flex justify-around items-center h-full pt-2">
            <a href="home.html" class="flex flex-col items-center space-y-1 transition-colors ${activePage === 'home' ? 'text-white' : 'text-gray-400 hover:text-gray-300'}">
                <svg class="w-7 h-7" fill="currentColor" viewBox="0 0 20 20">
                    <path d="M10.707 2.293a1 1 0 00-1.414 0l-7 7a1 1 0 001.414 1.414L4 10.414V17a1 1 0 001 1h2a1 1 0 001-1v-2a1 1 0 011-1h2a1 1 0 011 1v2a1 1 0 001 1h2a1 1 0 001-1v-6.586l.293.293a1 1 0 001.414-1.414l-7-7z"></path>
                </svg>
                <span class="text-xs ${activePage === 'home' ? 'font-bold' : ''}">Home</span>
            </a>
            <a href="drama.html" class="flex flex-col items-center space-y-1 transition-colors ${activePage === 'drama' ? 'text-white' : 'text-gray-400 hover:text-gray-300'}">
                <svg class="w-7 h-7" fill="currentColor" viewBox="0 0 20 20">
                    <path fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z" clip-rule="evenodd"></path>
                </svg>
                <span class="text-xs ${activePage === 'drama' ? 'font-bold' : ''}">Cari</span>
            </a>
            <a href="kategori.html" class="flex flex-col items-center space-y-1 transition-colors ${activePage === 'kategori' ? 'text-white' : 'text-gray-400 hover:text-gray-300'}">
                <svg class="w-7 h-7" fill="currentColor" viewBox="0 0 20 20">
                    <path d="M7 3a1 1 0 000 2h6a1 1 0 100-2H7zM4 7a1 1 0 011-1h10a1 1 0 110 2H5a1 1 0 01-1-1zM2 11a2 2 0 012-2h12a2 2 0 012 2v4a2 2 0 01-2 2H4a2 2 0 01-2-2v-4z"></path>
                </svg>
                <span class="text-xs ${activePage === 'kategori' ? 'font-bold' : ''}">Kategori</span>
            </a>
            <a href="favorit.html" class="flex flex-col items-center space-y-1 transition-colors ${activePage === 'favorit' ? 'text-white' : 'text-gray-400 hover:text-gray-300'}">
                <svg class="w-7 h-7" fill="currentColor" viewBox="0 0 20 20">
                    <path fill-rule="evenodd" d="M3.172 5.172a4 4 0 015.656 0L10 6.343l1.172-1.171a4 4 0 115.656 5.656L10 17.657l-6.828-6.829a4 4 0 010-5.656z" clip-rule="evenodd"></path>
                </svg>
                <span class="text-xs ${activePage === 'favorit' ? 'font-bold' : ''}">Favorit</span>
            </a>
            <a href="profil.html" class="flex flex-col items-center space-y-1 transition-colors ${activePage === 'profil' ? 'text-white' : 'text-gray-400 hover:text-gray-300'}">
                <svg class="w-7 h-7" fill="currentColor" viewBox="0 0 20 20">
                    <path fill-rule="evenodd" d="M10 9a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 1114 0H3z" clip-rule="evenodd"></path>
                </svg>
                <span class="text-xs ${activePage === 'profil' ? 'font-bold' : ''}">Profil</span>
            </a>
        </div>
    </nav>
    `;
    
    // Insert navbar di akhir body
    const navbar = document.createElement('div');
    navbar.innerHTML = navbarHTML;
    document.body.appendChild(navbar.firstElementChild);
}

// Auto-render navbar berdasarkan current page
document.addEventListener('DOMContentLoaded', function() {
    let currentPage = 'home';
    const pathname = window.location.pathname.split('/').pop() || 'home.html';
    
    if (pathname.includes('drama')) currentPage = 'drama';
    else if (pathname.includes('kategori')) currentPage = 'kategori';
    else if (pathname.includes('favorit')) currentPage = 'favorit';
    else if (pathname.includes('profil')) currentPage = 'profil';
    
    createNavbar(currentPage);
});
