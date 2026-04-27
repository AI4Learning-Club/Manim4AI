import React from 'react';
import {
  AbsoluteFill,
  Easing,
  OffthreadVideo,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

/* ================================================================== */
/*  Theme                                                              */
/* ================================================================== */

const hex2rgba = (hex, a = 1) => {
  const h = (hex || '#7C3AED').replace('#', '');
  const r = parseInt(h.substring(0, 2), 16);
  const g = parseInt(h.substring(2, 4), 16);
  const b = parseInt(h.substring(4, 6), 16);
  return `rgba(${r},${g},${b},${a})`;
};

const D = {
  accent: '#7C3AED',
  accent2: '#22D3EE',
  bg: '#0F172A',
  panel: 'rgba(15,23,42,0.72)',
  font: 'Inter, system-ui, sans-serif',
};
const T = (theme, k) => theme?.[k] || D[k];
const accent = (th) => T(th, 'accent');
const accent2 = (th) => T(th, 'accent2');
const bg = (th) => T(th, 'background') || D.bg;
const panel = (th) => T(th, 'panelBackground') || D.panel;
const font = (th) => T(th, 'fontFamily') || D.font;

const FADE = 18;

/* ================================================================== */
/*  Floating particles — persistent layer                              */
/* ================================================================== */

const PARTICLES = Array.from({length: 40}, (_, i) => {
  const seed = (i * 7919 + 104729) % 100000;
  return {
    x: (seed % 1920),
    y: ((seed * 3) % 1080),
    size: 2 + (seed % 4),
    speed: 0.15 + (seed % 100) / 400,
    opacity: 0.12 + (seed % 30) / 100,
    drift: ((seed % 200) - 100) / 800,
  };
});

const Particles = ({theme}) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{pointerEvents: 'none', zIndex: 1}}>
      {PARTICLES.map((p, i) => {
        const y = (p.y - frame * p.speed * 2) % 1120 - 40;
        const x = p.x + Math.sin(frame * p.drift) * 30;
        const col = i % 3 === 0 ? accent(theme) : i % 3 === 1 ? accent2(theme) : '#fff';
        return (
          <div
            key={i}
            style={{
              position: 'absolute',
              left: x,
              top: y,
              width: p.size,
              height: p.size,
              borderRadius: '50%',
              background: col,
              opacity: p.opacity,
              filter: p.size > 4 ? 'blur(1px)' : 'none',
            }}
          />
        );
      })}
    </AbsoluteFill>
  );
};

/* ================================================================== */
/*  Animated background with drifting gradient orbs                     */
/* ================================================================== */

const Background = ({theme}) => {
  const frame = useCurrentFrame();
  const {durationInFrames: dur} = useVideoConfig();
  const d = interpolate(frame, [0, dur], [0, 50]);
  const p = 0.35 + 0.15 * Math.sin(frame * 0.012);

  return (
    <AbsoluteFill style={{background: bg(theme)}}>
      <div style={{
        position: 'absolute', width: 1000, height: 1000, borderRadius: '50%',
        background: `radial-gradient(circle, ${hex2rgba(accent(theme), p)} 0%, transparent 68%)`,
        top: -300 + Math.sin(d * 0.03) * 80, left: -350 + Math.cos(d * 0.025) * 60,
        filter: 'blur(100px)',
      }}/>
      <div style={{
        position: 'absolute', width: 800, height: 800, borderRadius: '50%',
        background: `radial-gradient(circle, ${hex2rgba(accent2(theme), p * 0.6)} 0%, transparent 68%)`,
        bottom: -200 + Math.cos(d * 0.04) * 70, right: -250 + Math.sin(d * 0.035) * 50,
        filter: 'blur(110px)',
      }}/>
      <div style={{
        position: 'absolute', width: 500, height: 500, borderRadius: '50%',
        background: `radial-gradient(circle, ${hex2rgba(accent(theme), p * 0.3)} 0%, transparent 70%)`,
        top: '40%', left: '50%', transform: `translate(-50%, -50%) translate(${Math.sin(d * 0.05) * 40}px, ${Math.cos(d * 0.04) * 30}px)`,
        filter: 'blur(80px)',
      }}/>
    </AbsoluteFill>
  );
};

/* ================================================================== */
/*  Progress bar — top edge, always visible                            */
/* ================================================================== */

const ProgressBar = ({theme}) => {
  const frame = useCurrentFrame();
  const {durationInFrames: dur} = useVideoConfig();
  const progress = frame / Math.max(1, dur - 1);
  const fadeIn = interpolate(frame, [0, 40], [0, 1], {extrapolateRight: 'clamp'});

  return (
    <div style={{
      position: 'absolute', top: 0, left: 0, right: 0, height: 4, zIndex: 100,
      background: 'rgba(255,255,255,0.06)', opacity: fadeIn,
    }}>
      <div style={{
        height: '100%', width: `${progress * 100}%`,
        background: `linear-gradient(90deg, ${accent(theme)}, ${accent2(theme)})`,
        boxShadow: `0 0 12px ${hex2rgba(accent2(theme), 0.5)}`,
        borderRadius: '0 2px 2px 0',
      }}/>
    </div>
  );
};

/* ================================================================== */
/*  Lower-third caption bar — animated entry/exit per caption change   */
/* ================================================================== */

const LowerThird = ({captions, theme, globalOffset}) => {
  const frame = useCurrentFrame();
  const absFrame = frame + (globalOffset || 0);

  const caps = Array.isArray(captions) ? captions : [];
  const active = caps.find(c => absFrame >= c.start && absFrame < c.end);

  if (!active) return null;

  const localFrame = absFrame - active.start;
  const capDur = active.end - active.start;
  const enter = interpolate(localFrame, [0, 15], [0, 1], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  const exit = interpolate(localFrame, [capDur - 15, capDur], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const vis = Math.min(enter, exit);
  const slideY = interpolate(enter, [0, 1], [20, 0]);

  return (
    <div style={{
      position: 'absolute', bottom: 48, left: 0, right: 0, zIndex: 80,
      display: 'flex', justifyContent: 'center', pointerEvents: 'none',
      opacity: vis, transform: `translateY(${slideY}px)`,
    }}>
      <div style={{
        maxWidth: 1100, padding: '14px 32px',
        background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(16px)',
        borderRadius: 14,
        borderLeft: `3px solid ${accent2(theme)}`,
        color: 'white', fontSize: 26, lineHeight: 1.4,
        fontFamily: font(theme), fontWeight: 500,
        boxShadow: `0 8px 32px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.06)`,
      }}>
        {active.text}
      </div>
    </div>
  );
};

/* ================================================================== */
/*  Chapter sidebar — visible during core video                        */
/* ================================================================== */

const ChapterSidebar = ({chapters, captions, theme, globalOffset}) => {
  const frame = useCurrentFrame();
  const absFrame = frame + (globalOffset || 0);
  const caps = Array.isArray(captions) ? captions : [];
  const items = Array.isArray(chapters) && chapters.length > 0 ? chapters : caps.map(c => c.text?.split(':')[0] || '');

  if (items.length === 0) return null;

  const activeIdx = caps.findIndex(c => absFrame >= c.start && absFrame < c.end);
  const slideIn = interpolate(frame, [0, 30], [0, 1], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  const slideX = interpolate(slideIn, [0, 1], [-200, 0]);

  return (
    <div style={{
      position: 'absolute', top: 80, left: 0, zIndex: 60,
      display: 'flex', flexDirection: 'column', gap: 0,
      opacity: slideIn, transform: `translateX(${slideX}px)`,
      fontFamily: font(theme),
    }}>
      {items.slice(0, 6).map((ch, i) => {
        const isActive = i === activeIdx;
        return (
          <div key={i} style={{
            display: 'flex', alignItems: 'center', gap: 10,
            padding: '8px 20px 8px 16px',
            background: isActive ? hex2rgba(accent(theme), 0.25) : 'rgba(0,0,0,0.2)',
            backdropFilter: 'blur(8px)',
            borderLeft: isActive ? `3px solid ${accent2(theme)}` : '3px solid transparent',
            borderRadius: '0 10px 10px 0',
            marginBottom: 2,
            transition: 'all 0.3s ease',
          }}>
            <div style={{
              width: 8, height: 8, borderRadius: '50%',
              background: isActive ? accent2(theme) : 'rgba(255,255,255,0.2)',
              boxShadow: isActive ? `0 0 8px ${accent2(theme)}` : 'none',
              flexShrink: 0,
            }}/>
            <span style={{
              fontSize: 14, fontWeight: isActive ? 600 : 400,
              color: isActive ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.4)',
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
              maxWidth: 200,
            }}>
              {ch}
            </span>
          </div>
        );
      })}
    </div>
  );
};

/* ================================================================== */
/*  Branding strip — bottom edge                                       */
/* ================================================================== */

const BrandStrip = ({theme, title}) => {
  const frame = useCurrentFrame();
  const o = interpolate(frame, [20, 60], [0, 0.6], {extrapolateRight: 'clamp'});
  return (
    <div style={{
      position: 'absolute', bottom: 0, left: 0, right: 0, zIndex: 90,
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '10px 28px', opacity: o,
      background: 'linear-gradient(to top, rgba(0,0,0,0.3), transparent)',
      fontFamily: font(theme), fontSize: 13, color: 'rgba(255,255,255,0.35)',
    }}>
      <span>{title || ''}</span>
      <span style={{display: 'flex', alignItems: 'center', gap: 6}}>
        <span style={{
          width: 5, height: 5, borderRadius: '50%',
          background: accent2(theme),
          boxShadow: `0 0 6px ${accent2(theme)}`,
        }}/>
        Manim4AI
      </span>
    </div>
  );
};

/* ================================================================== */
/*  Glass panel                                                        */
/* ================================================================== */

const Glass = ({theme, children, style}) => (
  <div style={{
    background: panel(theme),
    backdropFilter: 'blur(28px)', WebkitBackdropFilter: 'blur(28px)',
    border: `1px solid ${hex2rgba(accent2(theme), 0.15)}`,
    borderRadius: 22,
    boxShadow: `0 20px 60px rgba(0,0,0,0.4), inset 0 1px 0 ${hex2rgba(accent(theme), 0.06)}`,
    ...style,
  }}>
    {children}
  </div>
);

/* ================================================================== */
/*  Wipe transition overlay — colored bar sweep between segments       */
/* ================================================================== */

const WipeTransition = ({theme, durationInFrames: dur}) => {
  const frame = useCurrentFrame();
  const enterEnd = Math.min(FADE + 5, dur / 2);
  const exitStart = dur - enterEnd;

  const enterWipe = interpolate(frame, [0, enterEnd], [-110, 110], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic),
  });
  const exitWipe = interpolate(frame, [exitStart, dur], [-110, 110], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic),
  });

  const enterOpacity = frame < enterEnd ? 1 : 0;
  const exitOpacity = frame > exitStart ? 1 : 0;

  return (
    <>
      {enterOpacity > 0 && (
        <AbsoluteFill style={{zIndex: 200, pointerEvents: 'none'}}>
          <div style={{
            position: 'absolute', top: 0, bottom: 0,
            left: `${enterWipe}%`, width: '12%',
            background: `linear-gradient(90deg, transparent, ${hex2rgba(accent(theme), 0.7)}, ${hex2rgba(accent2(theme), 0.5)}, transparent)`,
            filter: 'blur(2px)',
          }}/>
        </AbsoluteFill>
      )}
      {exitOpacity > 0 && (
        <AbsoluteFill style={{zIndex: 200, pointerEvents: 'none'}}>
          <div style={{
            position: 'absolute', top: 0, bottom: 0,
            left: `${exitWipe}%`, width: '12%',
            background: `linear-gradient(90deg, transparent, ${hex2rgba(accent2(theme), 0.5)}, ${hex2rgba(accent(theme), 0.7)}, transparent)`,
            filter: 'blur(2px)',
          }}/>
        </AbsoluteFill>
      )}
    </>
  );
};

/* ================================================================== */
/*  Crossfade wrapper                                                  */
/* ================================================================== */

const Crossfade = ({children, durationInFrames: dur}) => {
  const frame = useCurrentFrame();
  const fadeIn = interpolate(frame, [0, FADE], [0, 1], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  const fadeOut = interpolate(frame, [dur - FADE, dur], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill style={{opacity: Math.min(fadeIn, fadeOut)}}>
      {children}
    </AbsoluteFill>
  );
};

/* ================================================================== */
/*  Animated text — word-by-word spring reveal                         */
/* ================================================================== */

const AnimatedTitle = ({text, theme, delay = 0, fontSize = 56}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const words = (text || '').split(/\s+/);

  return (
    <div style={{display: 'flex', flexWrap: 'wrap', gap: '0 14px', lineHeight: 1.15}}>
      {words.map((word, i) => {
        const wordDelay = delay + i * 3;
        const s = spring({frame: Math.max(0, frame - wordDelay), fps, config: {damping: 16, mass: 0.5, stiffness: 120}});
        const y = interpolate(s, [0, 1], [40, 0]);
        return (
          <span key={i} style={{
            fontSize, fontWeight: 700, color: 'white',
            opacity: s, transform: `translateY(${y}px)`,
            display: 'inline-block',
          }}>
            {word}
          </span>
        );
      })}
    </div>
  );
};

/* ================================================================== */
/*  Staggered bullet list                                              */
/* ================================================================== */

const BulletList = ({bullets, theme, delay = 25}) => {
  const items = Array.isArray(bullets) ? bullets.filter(Boolean) : [];
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: 14, marginTop: 24}}>
      {items.map((b, i) => {
        const d = delay + i * 10;
        const s = spring({frame: Math.max(0, frame - d), fps, config: {damping: 18}});
        const x = interpolate(s, [0, 1], [40, 0]);
        return (
          <div key={i} style={{
            display: 'flex', gap: 12, alignItems: 'flex-start',
            fontSize: 28, lineHeight: 1.4, opacity: s,
            transform: `translateX(${x}px)`,
          }}>
            <span style={{
              color: accent2(theme), fontSize: 10, marginTop: 10, flexShrink: 0,
              width: 10, height: 10, borderRadius: '50%',
              background: `linear-gradient(135deg, ${accent(theme)}, ${accent2(theme)})`,
              display: 'inline-block',
            }}/>
            <span style={{color: 'rgba(255,255,255,0.88)'}}>{b}</span>
          </div>
        );
      })}
    </div>
  );
};

/* ================================================================== */
/*  Title card scene                                                   */
/* ================================================================== */

const TitleCard = ({segment, theme}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  const panelS = spring({frame, fps, config: {damping: 20, mass: 0.7}});
  const lineW = interpolate(frame, [5, 40], [0, 160], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  const bodyO = interpolate(frame, [18, 35], [0, 1], {extrapolateRight: 'clamp'});
  const labelO = interpolate(frame, [2, 15], [0, 1], {extrapolateRight: 'clamp'});

  return (
    <AbsoluteFill style={{
      justifyContent: 'center', alignItems: 'center',
      color: 'white', fontFamily: font(theme),
    }}>
      <Glass theme={theme} style={{
        width: 1100, padding: '56px 72px',
        transform: `scale(${0.9 + panelS * 0.1})`,
        opacity: panelS,
      }}>
        {/* Accent line */}
        <div style={{
          width: lineW, height: 3, borderRadius: 2,
          background: `linear-gradient(90deg, ${accent(theme)}, ${accent2(theme)})`,
          boxShadow: `0 0 12px ${hex2rgba(accent2(theme), 0.4)}`,
          marginBottom: 24,
        }}/>
        {/* Label */}
        <div style={{
          fontSize: 14, fontWeight: 600, letterSpacing: '0.1em', textTransform: 'uppercase',
          color: accent2(theme), marginBottom: 10, opacity: labelO,
        }}>
          LESSON OVERVIEW
        </div>
        {/* Title — word-by-word */}
        <AnimatedTitle text={segment.title} theme={theme} delay={6} fontSize={52}/>
        {/* Body */}
        <div style={{
          fontSize: 26, lineHeight: 1.5, marginTop: 18,
          color: 'rgba(255,255,255,0.75)', opacity: bodyO,
        }}>
          {segment.body}
        </div>
        <BulletList bullets={segment.bullets} theme={theme} delay={30}/>
      </Glass>
    </AbsoluteFill>
  );
};

/* ================================================================== */
/*  Summary card scene                                                 */
/* ================================================================== */

const SummaryCard = ({segment, theme}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  const panelS = spring({frame, fps, config: {damping: 20, mass: 0.7}});
  const lineW = interpolate(frame, [5, 40], [0, 160], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  const bodyO = interpolate(frame, [18, 35], [0, 1], {extrapolateRight: 'clamp'});
  const labelO = interpolate(frame, [2, 15], [0, 1], {extrapolateRight: 'clamp'});

  return (
    <AbsoluteFill style={{
      justifyContent: 'center', alignItems: 'center',
      color: 'white', fontFamily: font(theme),
    }}>
      <Glass theme={theme} style={{
        width: 1100, padding: '56px 72px',
        transform: `scale(${0.9 + panelS * 0.1})`,
        opacity: panelS,
      }}>
        <div style={{
          width: lineW, height: 3, borderRadius: 2,
          background: `linear-gradient(90deg, ${accent2(theme)}, ${accent(theme)})`,
          boxShadow: `0 0 12px ${hex2rgba(accent(theme), 0.4)}`,
          marginBottom: 24,
        }}/>
        <div style={{
          fontSize: 14, fontWeight: 600, letterSpacing: '0.1em', textTransform: 'uppercase',
          color: accent(theme), marginBottom: 10, opacity: labelO,
        }}>
          KEY TAKEAWAY
        </div>
        <AnimatedTitle text={segment.title} theme={theme} delay={6} fontSize={48}/>
        <div style={{
          fontSize: 26, lineHeight: 1.5, marginTop: 18,
          color: 'rgba(255,255,255,0.75)', opacity: bodyO,
        }}>
          {segment.body}
        </div>
        <BulletList bullets={segment.bullets} theme={theme} delay={30}/>
      </Glass>
    </AbsoluteFill>
  );
};

/* ================================================================== */
/*  Chapter card — bridge between Manim chunks                         */
/* ================================================================== */

const ChapterCard = ({segment, theme}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  const panelS = spring({frame, fps, config: {damping: 18, mass: 0.6}});
  const numS = spring({frame: Math.max(0, frame - 3), fps, config: {damping: 14, mass: 0.4, stiffness: 140}});
  const lineW = interpolate(frame, [3, 30], [0, 200], {extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  const bodyO = interpolate(frame, [15, 30], [0, 1], {extrapolateRight: 'clamp'});
  const chNum = segment.chapterNumber || '?';
  const total = segment.totalChapters || '?';

  return (
    <AbsoluteFill style={{
      justifyContent: 'center', alignItems: 'center',
      color: 'white', fontFamily: font(theme),
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 48,
        transform: `scale(${0.88 + panelS * 0.12})`, opacity: panelS,
      }}>
        {/* Big chapter number */}
        <div style={{
          fontSize: 140, fontWeight: 800, lineHeight: 1,
          background: `linear-gradient(135deg, ${accent(theme)}, ${accent2(theme)})`,
          WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
          opacity: numS, transform: `scale(${0.6 + numS * 0.4})`,
          textShadow: 'none', filter: `drop-shadow(0 0 30px ${hex2rgba(accent(theme), 0.3)})`,
        }}>
          {chNum}
        </div>

        {/* Content panel */}
        <Glass theme={theme} style={{
          padding: '40px 56px', maxWidth: 800,
        }}>
          <div style={{
            fontSize: 13, fontWeight: 600, letterSpacing: '0.1em', textTransform: 'uppercase',
            color: accent2(theme), marginBottom: 8, opacity: bodyO,
          }}>
            PART {chNum} OF {total}
          </div>
          <div style={{
            width: lineW, height: 2, borderRadius: 1,
            background: `linear-gradient(90deg, ${accent(theme)}, ${accent2(theme)})`,
            marginBottom: 16,
          }}/>
          <AnimatedTitle text={segment.title} theme={theme} delay={8} fontSize={42}/>
          {segment.body && (
            <div style={{
              fontSize: 24, lineHeight: 1.5, marginTop: 14,
              color: 'rgba(255,255,255,0.7)', opacity: bodyO,
            }}>
              {segment.body}
            </div>
          )}
          <BulletList bullets={segment.bullets} theme={theme} delay={22}/>
        </Glass>
      </div>
    </AbsoluteFill>
  );
};

const ConceptCard = ({segment, theme}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const panelS = spring({frame, fps, config: {damping: 16, mass: 0.65}});
  const badgeO = interpolate(frame, [0, 18], [0, 1], {extrapolateRight: 'clamp'});
  const bodyO = interpolate(frame, [10, 26], [0, 1], {extrapolateRight: 'clamp'});

  return (
    <AbsoluteFill style={{
      justifyContent: 'center',
      alignItems: 'center',
      color: 'white',
      fontFamily: font(theme),
      padding: 90,
    }}>
      <Glass theme={theme} style={{
        width: '100%',
        maxWidth: 1180,
        padding: '44px 52px',
        transform: `scale(${0.92 + panelS * 0.08})`,
        opacity: panelS,
      }}>
        <div style={{
          display: 'inline-flex',
          padding: '8px 14px',
          borderRadius: 999,
          fontSize: 14,
          fontWeight: 700,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          color: accent2(theme),
          background: hex2rgba(accent(theme), 0.18),
          marginBottom: 18,
          opacity: badgeO,
        }}>
          Concept Focus
        </div>
        <AnimatedTitle text={segment.title} theme={theme} delay={5} fontSize={46}/>
        {segment.body && (
          <div style={{
            marginTop: 16,
            fontSize: 28,
            lineHeight: 1.45,
            color: 'rgba(255,255,255,0.82)',
            opacity: bodyO,
            maxWidth: 920,
          }}>
            {segment.body}
          </div>
        )}
        <div style={{
          marginTop: 26,
          display: 'grid',
          gridTemplateColumns: '1.3fr 1fr',
          gap: 28,
          alignItems: 'start',
        }}>
          <BulletList bullets={segment.bullets} theme={theme} delay={20}/>
          <div style={{
            minHeight: 220,
            borderRadius: 22,
            border: `1px solid ${hex2rgba(accent2(theme), 0.22)}`,
            background: `linear-gradient(135deg, ${hex2rgba(accent(theme), 0.14)}, ${hex2rgba(accent2(theme), 0.1)})`,
            boxShadow: `inset 0 1px 0 ${hex2rgba('#ffffff', 0.06)}`,
            padding: '22px 24px',
            opacity: bodyO,
          }}>
            <div style={{
              fontSize: 16,
              textTransform: 'uppercase',
              letterSpacing: '0.08em',
              color: 'rgba(255,255,255,0.42)',
              marginBottom: 12,
            }}>
              Visual Direction
            </div>
            <div style={{
              fontSize: 22,
              lineHeight: 1.5,
              color: 'rgba(255,255,255,0.78)',
            }}>
              {segment.visualNotes || segment.body || 'Use kinetic typography, guided emphasis, and clean transitions.'}
            </div>
          </div>
        </div>
      </Glass>
    </AbsoluteFill>
  );
};

/* ================================================================== */
/*  Core Manim video scene — full video with overlays                  */
/* ================================================================== */

const ManimScene = ({segment, theme, captions, chapters, globalOffset}) => {
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();

  const revealS = spring({frame, fps, config: {damping: 22, mass: 0.5}});
  const glow = 0.3 + 0.2 * Math.sin(frame * 0.015);
  const pad = 6;

  return (
    <AbsoluteFill style={{fontFamily: font(theme), color: 'white'}}>
      <div style={{
        position: 'absolute', top: pad + 8, left: pad, right: pad, bottom: pad,
        borderRadius: 16, overflow: 'hidden',
        transform: `scale(${0.97 + revealS * 0.03})`,
        boxShadow: `0 0 ${30 + glow * 20}px ${hex2rgba(accent(theme), glow * 0.2)}`,
        border: `1px solid ${hex2rgba(accent(theme), 0.08 + glow * 0.05)}`,
      }}>
        <OffthreadVideo
          src={staticFile(segment.videoAsset)}
          style={{
            width: width - pad * 2,
            height: height - pad - 8,
            objectFit: 'contain',
            background: '#000',
          }}
        />
      </div>
      <ChapterSidebar chapters={chapters} captions={captions} theme={theme} globalOffset={globalOffset}/>
      <LowerThird captions={captions} theme={theme} globalOffset={globalOffset}/>
    </AbsoluteFill>
  );
};

/* ================================================================== */
/*  Manim chunk — plays a slice of the full video                      */
/* ================================================================== */

const ManimChunk = ({segment, theme}) => {
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();

  const revealS = spring({frame, fps, config: {damping: 22, mass: 0.5}});
  const glow = 0.3 + 0.15 * Math.sin(frame * 0.015);
  const pad = 6;
  const startSec = (segment.videoStartFrame || 0) / fps;

  return (
    <AbsoluteFill style={{fontFamily: font(theme), color: 'white'}}>
      <div style={{
        position: 'absolute', top: pad + 8, left: pad, right: pad, bottom: pad,
        borderRadius: 16, overflow: 'hidden',
        transform: `scale(${0.97 + revealS * 0.03})`,
        boxShadow: `0 0 ${30 + glow * 20}px ${hex2rgba(accent(theme), glow * 0.2)}`,
        border: `1px solid ${hex2rgba(accent(theme), 0.08 + glow * 0.05)}`,
      }}>
        <OffthreadVideo
          src={staticFile(segment.videoAsset)}
          startFrom={segment.videoStartFrame || 0}
          style={{
            width: width - pad * 2,
            height: height - pad - 8,
            objectFit: 'contain',
            background: '#000',
          }}
        />
      </div>
      {/* Floating chapter label */}
      {segment.title && (
        <div style={{
          position: 'absolute', top: pad + 18, left: pad + 16, zIndex: 10,
        }}>
          <Glass theme={theme} style={{
            padding: '6px 16px', borderRadius: 10,
            fontSize: 16, fontWeight: 600, color: 'rgba(255,255,255,0.8)',
          }}>
            {segment.title}
          </Glass>
        </div>
      )}
    </AbsoluteFill>
  );
};

/* ================================================================== */
/*  Segment dispatcher                                                 */
/* ================================================================== */

const RenderSegment = ({segment, theme, captions, chapters, globalOffset}) => {
  if (segment.type === 'manim_video') {
    return <ManimScene segment={segment} theme={theme} captions={captions} chapters={chapters} globalOffset={globalOffset}/>;
  }
  if (segment.type === 'manim_chunk') {
    return <ManimChunk segment={segment} theme={theme}/>;
  }
  if (segment.type === 'chapter_card') {
    return <ChapterCard segment={segment} theme={theme}/>;
  }
  if (segment.type === 'concept_card') {
    return <ConceptCard segment={segment} theme={theme}/>;
  }
  if (segment.type === 'summary_card') {
    return <SummaryCard segment={segment} theme={theme}/>;
  }
  return <TitleCard segment={segment} theme={theme}/>;
};

/* ================================================================== */
/*  Root composition                                                   */
/* ================================================================== */

export const HybridLesson = (props) => {
  const segments = Array.isArray(props?.segments) ? props.segments : [];
  const theme = props?.theme || {};
  const captions = props?.captions;
  const chapters = props?.chapters;
  const lessonTitle = props?.meta?.lessonGoal || '';

  const items = [];
  let cursor = 0;
  for (const seg of segments) {
    const dur = Number(seg?.durationInFrames || 1);
    items.push({...seg, from: cursor, dur});
    cursor += dur;
  }

  return (
    <AbsoluteFill>
      {/* L0: persistent animated background */}
      <Background theme={theme}/>

      {/* L1: persistent floating particles */}
      <Particles theme={theme}/>

      {/* L2: segment content */}
      {items.map((seg) => (
        <Sequence key={seg.id || `s-${seg.from}`} from={seg.from} durationInFrames={seg.dur}>
          <Crossfade durationInFrames={seg.dur}>
            <RenderSegment
              segment={seg}
              theme={theme}
              captions={captions}
              chapters={chapters}
              globalOffset={seg.from}
            />
          </Crossfade>
          <WipeTransition theme={theme} durationInFrames={seg.dur}/>
        </Sequence>
      ))}

      {/* L3: persistent progress bar */}
      <ProgressBar theme={theme}/>

      {/* L4: persistent brand strip */}
      <BrandStrip theme={theme} title={lessonTitle}/>
    </AbsoluteFill>
  );
};
