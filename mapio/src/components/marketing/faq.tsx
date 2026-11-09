export const FAQ_ITEMS: { q: string; a: string }[] = [
  {
    q: "Does MAPIO work in the Tesla browser?",
    a: "MAPIO is a web app designed for large landscape in-car browsers such as Tesla's. It runs in the browser like any website — there is nothing to install. Browser capabilities differ between car models and software versions (for example access to GPS, WebGL performance and background tabs), so we recommend trying it during the free trial. We publish our real-device test results as they become available.",
  },
  {
    q: "Is MAPIO made by or affiliated with Tesla?",
    a: "No. MAPIO is independent and is not affiliated with, endorsed by or sponsored by Tesla. MAPIO does not connect to your vehicle, read battery data or control the car's built-in navigation.",
  },
  {
    q: "How does Send to Car work?",
    a: "Open MAPIO in your car's browser, sign in and pair it by scanning a QR code with your phone. Then pick a destination on your phone and tap Send to Car — it appears in MAPIO in your car's browser, ready to route. It does not send anything to the car's built-in navigation.",
  },
  {
    q: "Does MAPIO use my location?",
    a: "Only if you allow it in your browser. Your position is used in your browser to show you on the map and to calculate routes. We do not store your live position history. See the Privacy Policy for details.",
  },
  {
    q: "Is there live traffic or live charger availability?",
    a: "Not today. Routes are calculated without live traffic, and charger availability is shown as unknown unless a source provides verified live status. We never invent this data.",
  },
  {
    q: "How does the free trial work?",
    a: "You start the trial through our payment provider Paddle with a payment method. You won't be charged until the trial ends; then the subscription renews monthly at the price shown at checkout until you cancel. You can cancel anytime from Account → Billing, and you keep access until the end of the paid or trial period.",
  },
  {
    q: "Which currencies and units does MAPIO support?",
    a: "MAPIO defaults to kilometres, Celsius and 24-hour time for Georgia, and you can switch to miles, Fahrenheit and 12-hour time. Exchange rates are official National Bank of Georgia reference rates, not bank buy/sell rates.",
  },
  {
    q: "Which languages are available?",
    a: "English and Georgian (ქართული). More languages may follow.",
  },
];

export function FaqList({ items = FAQ_ITEMS }: { items?: typeof FAQ_ITEMS }) {
  return (
    <div className="grid gap-3">
      {items.map((f) => (
        <details key={f.q} className="glass group p-5 [&_summary::-webkit-details-marker]:hidden">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-4 text-lg font-semibold">
            {f.q}
            <span aria-hidden className="text-2xl text-accent-2 transition-transform group-open:rotate-45">+</span>
          </summary>
          <p className="mt-3 leading-relaxed text-muted">{f.a}</p>
        </details>
      ))}
    </div>
  );
}
