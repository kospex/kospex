/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/templates/**/*.html",
    "./src/templates/**/*.js",
  ],
  theme: {
    extend: {
      colors: {
        // Custom colors for Kospex status indicators
        'status-active': '#4CAF50',
        'status-aging': '#FFF176', 
        'status-stale': '#A1887F',
        'status-dormant': '#E0E0E0',
        // Dark mode variants
        'status-dormant-dark': '#64748b',
      },
      fontFamily: {
        // Add custom fonts if needed
        'sans': ['Inter', 'system-ui', 'sans-serif'],
      },
      spacing: {
        // Custom spacing for charts and layouts
        '18': '4.5rem',
        '88': '22rem',
      },
      animation: {
        // Custom animations for chart interactions
        'fade-in': 'fadeIn 0.3s ease-in-out',
        'slide-in': 'slideIn 0.2s ease-out',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideIn: {
          '0%': { transform: 'translateY(-10px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
      },
      boxShadow: {
        // Custom shadows for cards and modals
        'card': '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)',
        'modal': '0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04)',
      },
      screens: {
        // Custom breakpoints for responsive design
        'xs': '475px',
        '3xl': '1600px',
      },
    },
  },
  plugins: [
    // Add Tailwind plugins if needed
    // require('@tailwindcss/forms'),
    // require('@tailwindcss/typography'),
  ],
  // Enable dark mode with class strategy
  darkMode: 'class',
  // Safelist important classes that might be added dynamically
  safelist: [
    'bg-status-active',
    'bg-status-aging', 
    'bg-status-stale',
    'bg-status-dormant',
    'bg-status-dormant-dark',
    'text-status-active',
    'text-status-aging',
    'text-status-stale', 
    'text-status-dormant',
    // Chart-related classes that might be added via JavaScript
    'opacity-80',
    'opacity-90',
    'stroke-2',
    'stroke-4',
    // DataTables responsive classes
    'dtr-control',
    'dtr-data',
    'child',
  ],
}