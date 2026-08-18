/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#171B19',
        paper: '#F5F6F2',
        surface: '#FFFFFF',
        muted: '#6E766F',
        line: '#E2E5DE',
        accent: {
          DEFAULT: '#2F6F5E',
          soft: '#DDEAE4',
          deep: '#1F4A3E',
        },
        danger: '#B23A34',
      },
      fontFamily: {
        display: ['"Sora"', 'sans-serif'],
        body: ['"Inter"', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'monospace'],
      },
      borderRadius: {
        sm: '6px',
        md: '10px',
        lg: '14px',
      },
    },
  },
  plugins: [],
}
