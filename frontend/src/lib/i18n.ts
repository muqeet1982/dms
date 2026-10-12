import i18n from "i18next";
import { initReactI18next } from "react-i18next";

// English keys only in v1; RTL/Urdu can be added by supplying another resource bundle.
i18n.use(initReactI18next).init({
  lng: "en",
  fallbackLng: "en",
  resources: { en: { translation: {} } },
  interpolation: { escapeValue: false },
  returnEmptyString: false,
});

export default i18n;
