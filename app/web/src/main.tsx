import { createRoot } from "react-dom/client";
import AuthShell from "./AuthShell";
import { CustomerLanguageProvider } from "./CustomerLanguageProvider";
import "./styles.css";
import "./customerMobileLayout.css";
import "./customerReading.css";
import "./customerResultDensity.css";

createRoot(document.getElementById("root")!).render(<CustomerLanguageProvider><AuthShell /></CustomerLanguageProvider>);
