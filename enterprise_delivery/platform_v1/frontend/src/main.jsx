import "@fontsource-variable/plus-jakarta-sans";
import React from "react";
import ReactDOM from "react-dom/client";
import EnterpriseControlHub from "../EnterpriseControlHub";
import { initializeAuth } from "./auth";
import "./styles.css";
const root = ReactDOM.createRoot(document.getElementById("root"));
initializeAuth()
  .then(() =>
    root.render(
      <React.StrictMode>
        <EnterpriseControlHub />
      </React.StrictMode>,
    ),
  )
  .catch(() =>
    root.render(
      <div className="auth-page">
        <div className="auth-card">
          <h1>Sign-in failed</h1>
          <p>The identity provider response could not be verified.</p>
          <a className="button primary" href="/">
            Return to sign in
          </a>
        </div>
      </div>,
    ),
  );
