import styles from "./hero.module.css";
import { HeroInstallCard } from "./client";

const links = [
  { number: "01", title: "Import", href: "#sources", description: "Bring the traces you already have." },
  { number: "02", title: "Replay", href: "#demo", description: "Run your workflows against a new model." },
  { number: "03", title: "Decide", href: "#verdicts", description: "See the evidence before you switch." },
];

export default function Hero() {
  return (
    <section className={styles.hero} aria-labelledby="hero-heading">
      <div className={styles.atmosphere} aria-hidden="true" />
      <div className={styles.route} aria-hidden="true"><span /><span /><span /></div>
      <div className={styles.inner}>
        <div className={styles.main}>
          <p className={styles.label}>SwitchIt! / workflow migration</p>
          <h1 id="hero-heading">Change the model.<br />Keep the workflow.</h1>
          <p className={styles.description}>Move between AI providers with evidence. Replay the work that matters, compare results, and see what needs attention before you switch.</p>
          <div className={styles.actions}>
            <a className={styles.button} href="#demo">Explore the demo <span aria-hidden="true">↗</span></a>
            <a className={styles.textLink} href="#install">Get started <span aria-hidden="true">↗</span></a>
          </div>
        </div>
        <div className={styles.side}>
          <HeroInstallCard />
          <nav className={styles.links} aria-label="Explore SwitchIt">
            {links.map((link) => (
              <a className={styles.row} href={link.href} key={link.href}>
                <span className={styles.rowTop}><span className={styles.number}>{link.number}</span><span aria-hidden="true">↗</span></span>
                <span className={styles.rowTitle}>{link.title}</span>
                <span className={styles.rowDescription}>{link.description}</span>
              </a>
            ))}
          </nav>
        </div>
      </div>
    </section>
  );
}
