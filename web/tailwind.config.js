/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          50: '#f6f7f9',
          100: '#eceef2',
          200: '#d5d9e1',
          300: '#b1b8c6',
          // every step from 400 up keeps >= 4.5:1 on white and on ink-50 (axe, WCAG AA)
          400: '#5f6b80',
          500: '#525d72',
          600: '#434b5d',
          700: '#363c4a',
          800: '#2d3240',
          900: '#262a35',
          950: '#171a21',
        },
        accent: {
          50: '#eef8ff',
          100: '#d9efff',
          200: '#bce3ff',
          300: '#8ed2ff',
          400: '#59b7ff',
          500: '#3396fc',
          600: '#1d77f1',
          700: '#1660de',
          800: '#184eb4',
          900: '#1a448d',
        },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
    },
  },
  plugins: [],
}
