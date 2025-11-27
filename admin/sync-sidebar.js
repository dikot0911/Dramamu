#!/usr/bin/env node
/**
 * Sidebar Sync Script
 * 
 * Membaca sidebar.html sebagai sumber tunggal dan meng-sync ke semua file HTML admin.
 * Jalankan: node sync-sidebar.js
 * 
 * Script ini akan:
 * 1. Membaca sidebar.html (Desktop + Mobile sidebar)
 * 2. Mengganti sidebar di semua file HTML admin
 * 3. Mempertahankan konten halaman lainnya
 */

const fs = require('fs');
const path = require('path');

const ADMIN_DIR = __dirname;
const SIDEBAR_FILE = path.join(ADMIN_DIR, 'sidebar.html');

const HTML_FILES = [
    'dashboard.html',
    'analytics.html', 
    'users.html',
    'movies.html',
    'requests.html',
    'withdrawals.html',
    'payments.html',
    'payment-settings.html',
    'settings.html',
    'admin-users.html'
];

const DESKTOP_SIDEBAR_START = '<!-- DESKTOP_SIDEBAR_START -->';
const DESKTOP_SIDEBAR_END = '<!-- DESKTOP_SIDEBAR_END -->';
const MOBILE_SIDEBAR_START = '<!-- MOBILE_SIDEBAR_START -->';
const MOBILE_SIDEBAR_END = '<!-- MOBILE_SIDEBAR_END -->';

function readSidebarSource() {
    const content = fs.readFileSync(SIDEBAR_FILE, 'utf8');
    
    const desktopMatch = content.match(/<aside class="sidebar" id="sidebar">[\s\S]*?<\/aside>/);
    const overlayMatch = content.match(/<div class="sidebar-overlay" id="sidebarOverlay"><\/div>/);
    const mobileMatch = content.match(/<aside class="sidebar-mobile" id="sidebarMobile">[\s\S]*?<\/aside>/);
    
    if (!desktopMatch || !mobileMatch) {
        console.error('ERROR: Tidak dapat menemukan sidebar di sidebar.html');
        process.exit(1);
    }
    
    const desktopSidebar = desktopMatch[0];
    const overlay = overlayMatch ? overlayMatch[0] : '<div class="sidebar-overlay" id="sidebarOverlay"></div>';
    const mobileSidebar = mobileMatch[0];
    
    return {
        desktop: `${DESKTOP_SIDEBAR_START}\n        ${desktopSidebar}\n        ${DESKTOP_SIDEBAR_END}`,
        mobile: `${MOBILE_SIDEBAR_START}\n${overlay}\n${mobileSidebar}\n${MOBILE_SIDEBAR_END}`
    };
}

function hasMarkers(content) {
    return content.includes(DESKTOP_SIDEBAR_START) && content.includes(MOBILE_SIDEBAR_START);
}

function replaceDesktopSidebar(content, newSidebar) {
    const regex = new RegExp(
        `${escapeRegex(DESKTOP_SIDEBAR_START)}[\\s\\S]*?${escapeRegex(DESKTOP_SIDEBAR_END)}`,
        'g'
    );
    return content.replace(regex, newSidebar);
}

function replaceMobileSidebar(content, newSidebar) {
    const regex = new RegExp(
        `${escapeRegex(MOBILE_SIDEBAR_START)}[\\s\\S]*?${escapeRegex(MOBILE_SIDEBAR_END)}`,
        'g'
    );
    return content.replace(regex, newSidebar);
}

function escapeRegex(string) {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function addMarkersToFile(content, sidebar) {
    let newContent = content;
    
    const desktopRegex = /<aside class="sidebar" id="sidebar">[\s\S]*?<\/aside>/;
    const desktopMatch = content.match(desktopRegex);
    if (desktopMatch) {
        newContent = newContent.replace(desktopRegex, sidebar.desktop);
    }
    
    const mobileRegex = /<!-- MOBILE SIDEBAR -->[\s\S]*?<\/aside>\s*<\/body>/;
    const mobileMatch = content.match(mobileRegex);
    if (mobileMatch) {
        newContent = newContent.replace(mobileRegex, `${sidebar.mobile}\n</body>`);
    } else {
        const mobileRegex2 = /<div class="sidebar-overlay" id="sidebarOverlay"><\/div>\s*<aside class="sidebar-mobile" id="sidebarMobile">[\s\S]*?<\/aside>\s*<\/body>/;
        const mobileMatch2 = content.match(mobileRegex2);
        if (mobileMatch2) {
            newContent = newContent.replace(mobileRegex2, `${sidebar.mobile}\n</body>`);
        } else {
            newContent = newContent.replace(/<\/body>/, `${sidebar.mobile}\n</body>`);
        }
    }
    
    return newContent;
}

function syncFile(filename, sidebar) {
    const filepath = path.join(ADMIN_DIR, filename);
    
    if (!fs.existsSync(filepath)) {
        console.log(`SKIP: ${filename} tidak ditemukan`);
        return false;
    }
    
    let content = fs.readFileSync(filepath, 'utf8');
    let newContent;
    
    if (hasMarkers(content)) {
        newContent = replaceDesktopSidebar(content, sidebar.desktop);
        newContent = replaceMobileSidebar(newContent, sidebar.mobile);
    } else {
        newContent = addMarkersToFile(content, sidebar);
    }
    
    fs.writeFileSync(filepath, newContent, 'utf8');
    console.log(`OK: ${filename} berhasil di-sync`);
    return true;
}

function main() {
    console.log('========================================');
    console.log('   SIDEBAR SYNC SCRIPT');
    console.log('========================================\n');
    
    console.log('Membaca sidebar.html...');
    const sidebar = readSidebarSource();
    console.log('OK: Sidebar source berhasil dibaca\n');
    
    console.log('Syncing ke semua file HTML...\n');
    
    let successCount = 0;
    let failCount = 0;
    
    for (const file of HTML_FILES) {
        if (syncFile(file, sidebar)) {
            successCount++;
        } else {
            failCount++;
        }
    }
    
    console.log('\n========================================');
    console.log(`   SELESAI: ${successCount} berhasil, ${failCount} gagal`);
    console.log('========================================\n');
    
    if (failCount > 0) {
        process.exit(1);
    }
}

main();
