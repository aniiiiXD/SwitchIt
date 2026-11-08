"use client";
import { useEffect, useRef, useState } from "react";
export function Brand() {
 return <a className="wordmark" href="/" aria-label="SwitchIt! home">SwitchIt!</a>;
}
export function Header() {
 const [open,setOpen]=useState(false);
 const toggle=useRef<HTMLButtonElement>(null);
 useEffect(()=>{
  const close=(e:KeyboardEvent)=>{if(e.key==="Escape"&&open){setOpen(false);toggle.current?.focus();}};
  document.addEventListener("keydown",close);return()=>document.removeEventListener("keydown",close);
 },[open]);
 return <header className="site-header"><div className="nav-inner"><Brand/><nav className="desktop-nav" aria-label="Main navigation"><a href="/#how">How it works</a><a href="/#sources">Integrations</a><a href="/#demo">Play the demo</a><a href="/#verdicts">The report</a></nav><div className="nav-actions"><a href="/guide/" className="guide-link">Read the guide</a><a href="/#install" className="nav-cta">Get SwitchIt!</a><button ref={toggle} className="menu-toggle" aria-expanded={open} aria-controls="mobile-navigation" aria-label={open?"Close menu":"Open menu"} onClick={()=>setOpen(!open)}><svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true"><path d={open?"M4 4l10 10M14 4 4 14":"M3 5h12M3 9h12M3 13h12"} stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg></button></div></div>{open&&<nav className="mobile-menu" id="mobile-navigation" aria-label="Mobile navigation" onClick={()=>setOpen(false)}><a href="/#how">How it works</a><a href="/#sources">Integrations</a><a href="/#demo">Play the demo</a><a href="/#verdicts">The report</a><a href="/guide/">Read the guide</a></nav>}</header>;
}
export function Footer() {
 return <footer className="site-footer"><div className="footer-main"><div><Brand/><p>Your workflows. Your choice of model.</p></div><nav className="footer-links" aria-label="Footer"><a href="/guide/">Documentation</a><a href="/#targets">Providers</a><a href="/LICENSE.txt">MIT license</a><a href="/#install">Get started</a></nav></div><div className="footer-signature" aria-hidden="true"><span>SwitchIt!</span><svg viewBox="0 0 100 100" fill="none"><path d="M15 85 85 15M15 15h70v70" stroke="currentColor" strokeWidth="3"/></svg></div><div className="footer-bottom"><span>Built for the freedom to switch.</span><span>Open source · Python 3.10+ · Local by default</span></div></footer>;
}
