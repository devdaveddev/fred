export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="auth-wrap">
      <div className="auth-side">
        <a href="/welcome" className="brand big">fred<span>.</span></a>
        <h2>Your analyst <span className="serif">team.</span></h2>
        <p>Business analyst, market researcher, founder&apos;s office, lead generator and marketing analyst. They work from your data every day and show their working.</p>
        <ul className="auth-points">
          <li>Trained on your own data, plus open pre-trained models</li>
          <li>Every number traceable to its source</li>
          <li>No numbers written by a chatbot</li>
        </ul>
      </div>
      <div className="auth-card">{children}</div>
    </div>
  );
}
