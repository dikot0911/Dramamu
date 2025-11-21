# Flowing Luxury Border - Design Documentation

## Design Name: "Flowing Luxury Border"

Desain border yang mengalir dengan gradient emas-ungu pada semua card di Dramamu Mini App.

---

## CSS Implementation

```css
.movie-card-premium::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  border-radius: 16px;
  padding: 1px;
  background: linear-gradient(
    135deg,
    #d4af37 0%,
    #8b5cf6 25%,
    #d4af37 50%,
    #8b5cf6 75%,
    #d4af37 100%
  );
  background-size: 200% 200%;
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  animation: borderFlow 8s linear infinite;
  pointer-events: none;
}

@keyframes borderFlow {
  0% {
    background-position: 0% 0%;
  }
  100% {
    background-position: 200% 0%;
  }
}
```

---

## Color Palette

| Element | Hex | Opacity |
|---------|-----|---------|
| Gold | #d4af37 | 100% |
| Purple | #8b5cf6 | 100% |

---

## Properties

- **Border Thickness**: 1px (padding: 1px)
- **Border Radius**: 16px
- **Animation Duration**: 8s
- **Animation Type**: linear infinite
- **Gradient Angle**: 135 degrees
- **CSS Mask**: XOR composite untuk show hanya border edge

---

## How It Works

Menggunakan pseudo-element `::before` dengan:
- Gradient animation yang mengalir
- CSS mask untuk menampilkan hanya border (bukan seluruh area)
- 8 detik loop untuk smooth infinite animation
- Z-index di bawah content

---

## File Location

- **CSS File**: `frontend/premium-styles.css`
- **Class**: `.movie-card-premium::before`
- **Digunakan di**: Semua halaman (home, drama, profil, favorit, dll)

---

**Status**: Production Ready
