import globals from "globals";
import hooks from "eslint-plugin-react-hooks";
export default [{
  files: ["src/**/*.js", "e2e/**/*.js", "playwright.config.js"],
  languageOptions: { ecmaVersion: "latest", sourceType: "module", globals: { ...globals.browser, ...globals.node } },
  plugins: { "react-hooks": hooks },
  rules: { "no-undef": "error", "react-hooks/rules-of-hooks": "error", "react-hooks/exhaustive-deps": "warn" },
}];
