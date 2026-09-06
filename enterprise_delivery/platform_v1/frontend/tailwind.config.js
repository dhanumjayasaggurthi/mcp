/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}', './EnterpriseControlHub.jsx'],
  theme: {
    extend: {
      colors: {
        'jj-red': '#EB1700',
        'jj-maroon-05': '#9E1B32',
        'jj-green-03': '#1E8E3E',
        'jj-yellow-01': '#FFD95A',
        'jj-gray-01': '#F7F7F6',
        'jj-gray-02': '#E8E7E5',
        'jj-gray-03': '#D2D0CD',
        'jj-gray-04': '#B5B2AF',
        'jj-gray-05': '#8B8783',
        'jj-gray-06': '#6E6965',
        'jj-gray-07': '#4F4A46',
        'jj-gray-08': '#312C2A'
      },
      fontFamily: {
        'johnson-display': ['Arial', 'Helvetica', 'sans-serif'],
        'johnson-text': ['Arial', 'Helvetica', 'sans-serif']
      }
    }
  },
  plugins: []
};
