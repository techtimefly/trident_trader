/* theme-picker.js — appearance control for the Trident dashboard.

   Builds the theme/font popover in the header, applies a choice by
   setting [data-theme] / [data-font] on <html>, and persists it in
   localStorage under tt-theme / tt-font. An inline <head> script in
   index.html applies the stored choice before first paint to avoid a
   flash; this file owns the canonical theme/font lists and the
   interactive picker. Outer-ring, cosmetic only — no network calls. */
(function () {
    'use strict';

    var THEME_KEY = 'tt-theme';
    var FONT_KEY = 'tt-font';
    var DEFAULT_THEME = 'midnight';
    var DEFAULT_FONT = 'modern';

    // Canonical lists. `swatch` is [bg, panel-2, green, indigo] — four
    // representative colors previewed next to each theme name. Keep these
    // in sync with the [data-theme] blocks in themes.css.
    var THEMES = [
        { key: 'midnight', label: 'Midnight', swatch: ['#0a0a0b', '#18181c', '#34d399', '#818cf8'] },
        { key: 'graphite', label: 'Graphite', swatch: ['#0d1117', '#21262d', '#3fb950', '#a371f7'] },
        { key: 'nord',     label: 'Nord',     swatch: ['#2e3440', '#3b4252', '#a3be8c', '#88c0d0'] },
        { key: 'paper',    label: 'Paper',    swatch: ['#f4f4f3', '#ececeb', '#15803d', '#4f46e5'] },
        { key: 'amber',    label: 'Amber',    swatch: ['#0b0a07', '#1d1810', '#ffb224', '#6ee7a8'] }
    ];
    var FONTS = [
        { key: 'modern', label: 'Modern',    family: "'Inter', sans-serif" },
        { key: 'system', label: 'System',    family: 'system-ui, sans-serif' },
        { key: 'plex',   label: 'IBM Plex',  family: "'IBM Plex Sans', sans-serif" },
        { key: 'mono',   label: 'Monospace', family: "'JetBrains Mono', monospace" }
    ];

    var root = document.documentElement;
    var themeKeys = THEMES.map(function (t) { return t.key; });
    var fontKeys = FONTS.map(function (f) { return f.key; });

    function read(key, fallback, valid) {
        var v = null;
        try { v = localStorage.getItem(key); } catch (e) { v = null; }
        return (v && valid.indexOf(v) !== -1) ? v : fallback;
    }
    function write(key, value) {
        // Private-browsing / disabled storage: the choice just won't persist.
        try { localStorage.setItem(key, value); } catch (e) { /* ignore */ }
    }

    var current = {
        theme: read(THEME_KEY, DEFAULT_THEME, themeKeys),
        font: read(FONT_KEY, DEFAULT_FONT, fontKeys)
    };
    // Re-assert the validated choice — corrects a stale or unknown value
    // the inline head script may have applied verbatim.
    root.setAttribute('data-theme', current.theme);
    root.setAttribute('data-font', current.font);

    function group(label) {
        var g = document.createElement('div');
        g.className = 'tt-pop-group';
        g.setAttribute('role', 'radiogroup');
        g.setAttribute('aria-label', label);
        var l = document.createElement('div');
        l.className = 'tt-pop-label';
        l.textContent = label;
        g.appendChild(l);
        return g;
    }

    function optionRow(label) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'tt-opt';
        b.setAttribute('role', 'radio');
        var name = document.createElement('span');
        name.className = 'tt-opt-name';
        name.textContent = label;
        var check = document.createElement('span');
        check.className = 'tt-opt-check';
        check.textContent = '✓';
        b.appendChild(name);
        b.appendChild(check);
        return b;
    }

    function refresh() {
        var opts = document.querySelectorAll('.tt-opt');
        for (var i = 0; i < opts.length; i++) {
            var o = opts[i];
            var selected = o.dataset.themeKey
                ? o.dataset.themeKey === current.theme
                : o.dataset.fontKey === current.font;
            o.setAttribute('aria-checked', selected ? 'true' : 'false');
        }
    }

    function build() {
        var pop = document.getElementById('theme-pop');
        var btn = document.getElementById('theme-btn');
        if (!pop || !btn) return;

        var themeGroup = group('Color theme');
        THEMES.forEach(function (t) {
            var opt = optionRow(t.label);
            opt.dataset.themeKey = t.key;
            var sw = document.createElement('span');
            sw.className = 'tt-swatch';
            t.swatch.forEach(function (c) {
                var s = document.createElement('span');
                s.style.background = c;
                sw.appendChild(s);
            });
            opt.insertBefore(sw, opt.firstChild);
            opt.addEventListener('click', function () {
                current.theme = t.key;
                root.setAttribute('data-theme', t.key);
                write(THEME_KEY, t.key);
                refresh();
            });
            themeGroup.appendChild(opt);
        });

        var fontGroup = group('Font style');
        FONTS.forEach(function (f) {
            var opt = optionRow(f.label);
            opt.dataset.fontKey = f.key;
            opt.querySelector('.tt-opt-name').style.fontFamily = f.family;
            opt.addEventListener('click', function () {
                current.font = f.key;
                root.setAttribute('data-font', f.key);
                write(FONT_KEY, f.key);
                refresh();
            });
            fontGroup.appendChild(opt);
        });

        pop.appendChild(themeGroup);
        pop.appendChild(fontGroup);
        refresh();

        function toggle(open) {
            pop.classList.toggle('open', open);
            btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        }
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            toggle(!pop.classList.contains('open'));
        });
        pop.addEventListener('click', function (e) { e.stopPropagation(); });
        document.addEventListener('click', function () { toggle(false); });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') toggle(false);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', build);
    } else {
        build();
    }
})();
