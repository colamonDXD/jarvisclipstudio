import { ImageResponse } from "next/og";

const FONT_SOURCES = [
  {
    name: "Syne",
    url: "https://fonts.gstatic.com/s/syne/v22/8vIS7w4qzmVxsWxjBZRjr0FKM_04uQ6OQly_aA.woff",
    weight: 800,
  },
  {
    name: "Geist",
    url: "https://fonts.gstatic.com/s/geist/v1/gyBhhwUxId8gMGYQMKR3pzfaWI_RnOI.woff",
    weight: 400,
  },
] as const;

type SocialImageFontWeight = (typeof FONT_SOURCES)[number]["weight"];

type SocialImageFont = {
  name: string;
  data: ArrayBuffer;
  style: "normal";
  weight: SocialImageFontWeight;
};

async function loadFont(
  name: string,
  url: string,
  weight: SocialImageFontWeight
): Promise<SocialImageFont | null> {
  try {
    const response = await fetch(new URL(url));

    if (!response.ok) {
      console.warn(`Failed to load ${name} font for social image`, {
        status: response.status,
        url,
      });
      return null;
    }

    return {
      name,
      data: await response.arrayBuffer(),
      style: "normal",
      weight,
    };
  } catch (error) {
    console.warn(`Failed to load ${name} font for social image`, {
      error,
      url,
    });
    return null;
  }
}

async function loadFonts(): Promise<SocialImageFont[]> {
  const fonts = await Promise.all(
    FONT_SOURCES.map(({ name, url, weight }) => loadFont(name, url, weight))
  );

  return fonts.filter((font): font is SocialImageFont => font !== null);
}

export async function createSocialImageResponse() {
  const fonts = await loadFonts();

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          background: "linear-gradient(145deg, #F4F2FF 0%, #E9E6FF 50%, #DCD6FF 100%)",
          position: "relative",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            backgroundImage:
              "linear-gradient(rgba(0,0,0,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(0,0,0,0.03) 1px, transparent 1px)",
            backgroundSize: "40px 40px",
          }}
        />

        <div
          style={{
            position: "absolute",
            top: -80,
            right: -80,
            width: 320,
            height: 320,
            borderRadius: "50%",
            border: "1px solid rgba(0,0,0,0.06)",
            display: "flex",
          }}
        />
        <div
          style={{
            position: "absolute",
            bottom: -120,
            left: -60,
            width: 400,
            height: 400,
            borderRadius: "50%",
            border: "1px solid rgba(0,0,0,0.04)",
            display: "flex",
          }}
        />

        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 28,
            padding: "48px 64px",
            position: "relative",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 88,
              height: 88,
              borderRadius: 20,
              background: "linear-gradient(135deg, #241E4D 0%, #171233 100%)",
              boxShadow: "0 8px 32px rgba(0,0,0,0.15), 0 2px 8px rgba(0,0,0,0.1)",
            }}
          >
            <svg
              width="48"
              height="48"
              viewBox="0 0 24 24"
              fill="none"
              stroke="#F4F2FF"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="6" cy="6" r="3" />
              <circle cx="6" cy="18" r="3" />
              <line x1="20" y1="4" x2="8.12" y2="15.88" />
              <line x1="14.47" y1="14.48" x2="20" y2="20" />
              <line x1="8.12" y1="8.12" x2="12" y2="12" />
            </svg>
          </div>

          <div
            style={{
              display: "flex",
              alignItems: "baseline",
              fontFamily: "Syne",
              fontSize: 72,
              fontWeight: 800,
              color: "#171233",
              letterSpacing: "-2px",
              lineHeight: 1,
            }}
          >
            Supo
            <span
              style={{
                color: "#5C5C72",
              }}
            >
              Clip
            </span>
          </div>

          <div
            style={{
              display: "flex",
              fontFamily: "Geist",
              fontSize: 26,
              color: "#6E6E84",
              letterSpacing: "-0.3px",
              lineHeight: 1,
            }}
          >
            Turn long videos into viral-ready shorts
          </div>

          <div
            style={{
              display: "flex",
              gap: 12,
              marginTop: 8,
            }}
          >
            {["AI-Powered", "Auto Subtitles", "9:16 Vertical", "Open Source"].map(
              (label) => (
                <div
                  key={label}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    padding: "8px 18px",
                    borderRadius: 100,
                    background: "rgba(0,0,0,0.05)",
                    border: "1px solid rgba(0,0,0,0.08)",
                    fontFamily: "Geist",
                    fontSize: 15,
                    color: "#3D3D55",
                    letterSpacing: "-0.2px",
                  }}
                >
                  {label}
                </div>
              )
            )}
          </div>
        </div>

        <div
          style={{
            position: "absolute",
            bottom: 0,
            left: 0,
            right: 0,
            height: 4,
            background: "linear-gradient(90deg, #241E4D 0%, #7C5CFF 50%, #241E4D 100%)",
            display: "flex",
          }}
        />
      </div>
    ),
    {
      width: 1200,
      height: 630,
      ...(fonts.length > 0 ? { fonts } : {}),
    }
  );
}
