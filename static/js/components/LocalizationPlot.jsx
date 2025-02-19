import React, { useEffect, useRef } from "react";
import { useSelector } from "react-redux";
import PropTypes from "prop-types";
import CircularProgress from "@mui/material/CircularProgress";

import makeStyles from "@mui/styles/makeStyles";

import { geoMollweide } from "d3-geo-projection";
import * as d3 from "d3";
import d3GeoZoom from "d3-geo-zoom";
// eslint-disable-next-line
import GeoPropTypes from "geojson-prop-types";

const useStyles = makeStyles(() => ({
  fieldStyle: {
    stroke: "blue",
    strokeWidth: "0.5",
  },
}));
const LocalizationPlot = ({
  localization,
  sources,
  galaxies,
  instrument,
  observations,
  options,
  height,
  width,
  rotation,
  setRotation,
  selectedFields,
  setSelectedFields,
  type,
  projection = "orthographic",
}) => {
  const { analysisLoc } = useSelector((state) => state.localization);
  const { obsplanLoc } = useSelector((state) => state.localization);

  if (!localization) {
    if (type === "obsplan") {
      localization = obsplanLoc;
    } else if (type === "analysis") {
      localization = analysisLoc;
    }
  }

  if (
    !localization?.id ||
    !localization?.dateobs ||
    !localization?.localization_name ||
    !localization?.contour
  ) {
    return <CircularProgress />;
  }

  if (!rotation && localization?.contour?.features[0].geometry.coordinates) {
    const center = localization?.contour?.features[0].geometry.coordinates || [
      0, 0,
    ];
    rotation = [360 - center[0], -center[1], 0];
  }

  return (
    <GeoJSONGlobePlot
      skymap={localization?.contour}
      sources={sources?.geojson}
      galaxies={galaxies?.geojson}
      instrument={instrument}
      observations={observations?.geojson}
      options={options}
      height={height}
      width={width}
      rotation={rotation}
      setRotation={setRotation}
      selectedFields={selectedFields}
      setSelectedFields={setSelectedFields}
      projectionType={projection}
    />
  );
};

LocalizationPlot.propTypes = {
  localization: PropTypes.shape({
    id: PropTypes.number,
    dateobs: PropTypes.string,
    localization_name: PropTypes.string,
    contour: GeoPropTypes.FeatureCollection,
  }),
  sources: PropTypes.shape({
    geojson: GeoPropTypes.FeatureCollection,
    sources: PropTypes.arrayOf(
      PropTypes.shape({
        id: PropTypes.string,
        ra: PropTypes.number,
        dec: PropTypes.number,
        origin: PropTypes.string,
        alias: PropTypes.arrayOf(PropTypes.string),
        redshift: PropTypes.number,
        classifications: PropTypes.arrayOf(
          PropTypes.shape({
            id: PropTypes.number,
            classification: PropTypes.string,
            created_at: PropTypes.string,
            groups: PropTypes.arrayOf(
              PropTypes.shape({
                id: PropTypes.number,
                name: PropTypes.string,
              })
            ),
          })
        ),
        recent_comments: PropTypes.arrayOf(PropTypes.shape({})),
        altdata: PropTypes.shape({
          tns: PropTypes.shape({
            name: PropTypes.string,
          }),
        }),
        spectrum_exists: PropTypes.bool,
        last_detected_at: PropTypes.string,
        last_detected_mag: PropTypes.number,
        peak_detected_at: PropTypes.string,
        peak_detected_mag: PropTypes.number,
        groups: PropTypes.arrayOf(
          PropTypes.shape({
            id: PropTypes.number,
            name: PropTypes.string,
          })
        ),
      })
    ),
  }),
  galaxies: PropTypes.shape({
    geojson: GeoPropTypes.FeatureCollection,
    galaxies: PropTypes.arrayOf(
      PropTypes.shape({
        catalog_name: PropTypes.string,
        name: PropTypes.string,
        alt_name: PropTypes.string,
        ra: PropTypes.number,
        dec: PropTypes.number,
        distmpc: PropTypes.number,
        distmpc_unc: PropTypes.number,
        redshift: PropTypes.number,
        redshift_error: PropTypes.number,
        sfr_fuv: PropTypes.number,
        mstar: PropTypes.number,
        magb: PropTypes.number,
        magk: PropTypes.number,
        a: PropTypes.number,
        b2a: PropTypes.number,
        pa: PropTypes.number,
        btc: PropTypes.number,
      })
    ),
  }),
  instrument: PropTypes.shape({
    name: PropTypes.string,
    type: PropTypes.string,
    band: PropTypes.string,
    fields: PropTypes.arrayOf(
      PropTypes.shape({
        ra: PropTypes.number,
        dec: PropTypes.number,
        id: PropTypes.number,
      })
    ),
  }),
  observations: PropTypes.shape({
    geojson: PropTypes.arrayOf(
      PropTypes.oneOfType([
        GeoPropTypes.FeatureCollection,
        PropTypes.shape({
          type: PropTypes.string,
          features: PropTypes.array, // eslint-disable-line react/forbid-prop-types
        }),
      ])
    ),
    observations: PropTypes.arrayOf(
      PropTypes.shape({
        id: PropTypes.string,
        obstime: PropTypes.instanceOf(Date),
        filt: PropTypes.string,
        exposure_time: PropTypes.number,
        airmass: PropTypes.number,
        limmag: PropTypes.number,
        seeing: PropTypes.number,
        processed_fraction: PropTypes.number,
      })
    ),
  }),
  options: PropTypes.shape({
    skymap: PropTypes.bool,
    sources: PropTypes.bool,
    galaxies: PropTypes.bool,
    instrument: PropTypes.bool,
    observations: PropTypes.bool,
  }),
  height: PropTypes.number,
  width: PropTypes.number,
  rotation: PropTypes.arrayOf(PropTypes.number),
  setRotation: PropTypes.func,
  selectedFields: PropTypes.arrayOf(PropTypes.number),
  setSelectedFields: PropTypes.func,
  type: PropTypes.string,
  projection: PropTypes.string,
};

LocalizationPlot.defaultProps = {
  localization: null,
  sources: null,
  galaxies: null,
  instrument: null,
  observations: null,
  options: {
    skymap: false,
    sources: false,
    galaxies: false,
    instrument: false,
    observations: false,
  },
  height: 600,
  width: 600,
  rotation: null,
  setRotation: () => {},
  selectedFields: [],
  setSelectedFields: () => {},
  type: null,
  projection: "orthographic",
};

const useD3 = (renderer, height, width, data) => {
  const svgRef = useRef();

  useEffect(() => {
    if (data) {
      renderer(d3.select(svgRef.current), height, width, data);
    }
  }, [renderer, svgRef, data, height, width]);

  return svgRef;
};

const GeoJSONGlobePlot = ({
  skymap,
  sources,
  galaxies,
  instrument,
  observations,
  options,
  height,
  width,
  rotation,
  setRotation,
  selectedFields,
  setSelectedFields,
  airmass_threshold = 2.5,
  projectionType = "orthographic",
}) => {
  const projectionTypeVisibilities = {
    orthographic: 1.57,
    mollweide: 3.14,
  };
  const classes = useStyles();
  function renderMap(svg, svgheight, svgwidth, data) {
    const center = [svgwidth / 2, svgheight / 2];
    let projection = null;
    if (projectionType === "orthographic") {
      projection = d3.geoOrthographic().translate(center).scale(300);
    } else if (projectionType === "mollweide") {
      projection = geoMollweide().translate(center).scale(100);
    } else {
      throw new Error("Invalid projection type");
    }
    if (rotation && projectionType === "orthographic") {
      projection.rotate(rotation);
    }
    const geoGenerator = d3.geoPath().projection(projection);
    const graticule = d3.geoGraticule();
    // Draw text labels
    const translate = (d) => {
      const coord = projection(d.geometry.coordinates);
      return `translate(${coord[0]}, ${coord[1] - 10})`;
    };

    const visibleOnSphere = (d) => {
      if (!d.properties?.name && !d.properties?.credible_level === 0)
        return false;
      if (!d.geometry?.coordinates) return false;

      const gdistance = d3.geoDistance(
        d.geometry.coordinates,
        projection.invert(center)
      );

      // In front of globe?
      return gdistance < projectionTypeVisibilities[projectionType];
    };

    const filtersToColor = (filters) => {
      const filterStr = filters.join("");
      let hash = 0;
      for (let i = 0; i < filterStr.length; i += 1) {
        // eslint-disable-next-line no-bitwise
        hash = filterStr.charCodeAt(i) + ((hash << 5) - hash);
      }
      let color = "#";
      for (let i = 0; i < 3; i += 1) {
        // eslint-disable-next-line no-bitwise
        const value = (hash >> (i * 8)) & 0xff;
        color += `00${value.toString(16)}`.substr(-2);
      }
      return color;
    };

    function refresh() {
      if (projectionType === "mollweide") {
        // we dont allow the user to move the globe in mollweide projection
        const currentRotation = projection.rotate();
        projection.rotate([currentRotation[0], currentRotation[1], 0]);
      }
      svg.selectAll("*").remove();

      svg
        .datum({ type: "Sphere" })
        .append("path")
        .style("fill", "aliceblue")
        .style("stroke", "none")
        .style("opacity", 1)
        .attr("d", geoGenerator);

      // Draw grid
      svg
        .data([graticule()])
        .append("path")
        .style("fill", "none")
        .style("stroke", "darkgray")
        .style("stroke-width", "0.5px")
        .attr("d", geoGenerator);

      // draw markers on the grid lines at the equator to indicate the angles
      const angles = [-90, 0, 90, 180];
      const angleMarkers = angles.map((ra) => ({
        type: "Feature",
        geometry: {
          type: "Point",
          coordinates: [ra, 0],
        },
        properties: {
          name: `${ra}°`,
        },
      }));
      svg
        .selectAll("angleMarker")
        .data(angleMarkers)
        .enter()
        .append("circle")
        .attr("fill", "darkgray")
        .attr("cx", (d) => projection(d.geometry.coordinates)[0])
        .attr("cy", (d) => projection(d.geometry.coordinates)[1])
        .attr("r", 4)
        .style("visibility", (d) =>
          visibleOnSphere(d) ? "visible" : "hidden"
        );

      svg
        .selectAll(".label")
        .data(angleMarkers)
        .enter()
        .append("text")
        .attr("transform", translate)
        .style("visibility", (d) => (visibleOnSphere(d) ? "visible" : "hidden"))
        .text((d) => d.properties.name);

      if (data.skymap?.features && data.options.localization) {
        const x = (d) => projection(d.geometry.coordinates)[0];
        const y = (d) => projection(d.geometry.coordinates)[1];

        // Draw the center (0th most probable region)
        svg
          .selectAll("circle_skymap")
          .data([data.skymap.features[0]])
          .enter()
          .append("circle")
          .attr("fill", (d) => (visibleOnSphere(d) ? "blue" : "none"))
          .attr("cx", x)
          .attr("cy", y)
          .attr("r", 3);

        svg
          .selectAll(".label")
          .data([data.skymap.features[0]])
          .enter()
          .append("text")
          .attr("transform", translate)
          .style("visibility", (d) =>
            visibleOnSphere(d) ? "visible" : "hidden"
          )
          .style("text-anchor", "middle")
          .style("font-size", "0.75rem")
          .style("font-weight", "normal")
          .text("Center");

        // Draw the contour (90th most probable region)
        svg
          .selectAll("path")
          .data(data.skymap.features)
          .enter()
          .append("path")
          .attr("class", (d) => d.properties.name)
          .attr("d", geoGenerator)
          .style("fill", "none")
          .style("stroke", "black")
          .style("stroke-width", "0.5px");
      }

      if (data.instrument?.fields && data.options.instrument) {
        const filterColor = filtersToColor(data.instrument?.filters);

        data.instrument.fields.sort((a, b) => {
          if (a.field_id < b.field_id) {
            return 1;
          }
          if (a.field_id > b.field_id) {
            return -1;
          }
          return 0;
        });

        data.instrument.fields.forEach((f) => {
          const { field_id } = f;
          const { features } = f.contour_summary;
          const selected = selectedFields.includes(Number(f.id));
          const { airmass } = f;
          svg
            .data(features)
            .append("path")
            .attr("class", field_id)
            .classed(classes.fieldStyle, true)
            .style(
              "fill",
              // eslint-disable-next-line no-nested-ternary
              selected
                ? filterColor
                : airmass && airmass < airmass_threshold
                ? "white"
                : "gray"
            )
            .attr("d", geoGenerator)
            .on("click", () => {
              if (!selected) {
                setSelectedFields([...selectedFields, Number(field_id)]);
              } else {
                setSelectedFields(
                  selectedFields.filter((id) => id !== Number(field_id))
                );
              }
              refresh();
              setRotation(projection.rotate());
            })
            .append("title")
            .text(
              `field ID: ${field_id} \nra: ${f.ra} \ndec: ${
                f.dec
              } \nfilters: ${data.instrument.filters.join(", ")}`
            );
        });
      }

      if (data.observations && data.options.observations) {
        data.observations.forEach((f) => {
          const { field_id } = f.properties;
          const { features } = f;
          svg
            .data(features)
            .append("path")
            .attr("class", field_id)
            .style("fill", f.selected ? "red" : "white")
            .style("stroke", "blue")
            .style("stroke-width", "0.5px")
            .attr("d", geoGenerator)
            .on("click", () => {
              if (f.selected) {
                f.selected = false;
              } else {
                f.selected = true;
              }
              refresh();
            });
        });
      }

      if (data.sources?.features && data.options.sources) {
        svg
          .selectAll(".label")
          .data(data.sources.features)
          .enter()
          .append("a")
          .attr("xlink:href", (d) => d.properties.url)
          .append("text")
          .attr("transform", translate)
          .style("visibility", (d) =>
            visibleOnSphere(d) ? "visible" : "hidden"
          )
          .style("text-anchor", "middle")
          .style("font-size", "0.75rem")
          .style("font-weight", "normal")
          .text((d) => d.properties.name);

        const x = (d) => projection(d.geometry.coordinates)[0];
        const y = (d) => projection(d.geometry.coordinates)[1];

        svg
          .selectAll("circle_sources")
          .data(sources.features)
          .enter()
          .append("circle")
          .attr("fill", (d) => (visibleOnSphere(d) ? "red" : "none"))
          .attr("cx", x)
          .attr("cy", y)
          .attr("r", 3);
      }

      if (data.galaxies?.features && data.options.galaxies) {
        svg
          .selectAll(".label")
          .data(data.galaxies.features)
          .enter()
          .append("a")
          .attr("xlink:href", (d) => d.properties.url)
          .append("text")
          .attr("transform", translate)
          .style("visibility", (d) =>
            visibleOnSphere(d) ? "visible" : "hidden"
          )
          .style("text-anchor", "middle")
          .style("font-size", "0.75rem")
          .style("font-weight", "normal")
          .text((d) => d.properties.name);

        const x = (d) => projection(d.geometry.coordinates)[0];
        const y = (d) => projection(d.geometry.coordinates)[1];

        svg
          .selectAll("circle")
          .data(galaxies.features)
          .enter()
          .append("circle")
          .attr("fill", (d) => (visibleOnSphere(d) ? "green" : "none"))
          .attr("cx", x)
          .attr("cy", y)
          .attr("r", 3)
          .append("title")
          .text(
            (d) =>
              `coordinates: ${d.geometry.coordinates[0]}, ${d.geometry.coordinates[1]}`
          );
      }
    }

    refresh();
    d3GeoZoom().projection(projection).onMove(refresh)(svg.node());
  }

  const data = {
    skymap,
    sources,
    galaxies,
    instrument,
    observations,
    options,
  };

  const svgRef = useD3(renderMap, height, width, data);

  return (
    <svg
      preserveAspectRatio="xMidYMid meet"
      viewBox={`0 0 ${height} ${width}`}
      ref={svgRef}
    />
  );
};

GeoJSONGlobePlot.propTypes = {
  skymap: GeoPropTypes.FeatureCollection,
  sources: GeoPropTypes.FeatureCollection,
  galaxies: GeoPropTypes.FeatureCollection,
  instrument: PropTypes.shape({
    name: PropTypes.string,
    type: PropTypes.string,
    band: PropTypes.string,
    fields: PropTypes.arrayOf(
      PropTypes.shape({
        ra: PropTypes.number,
        dec: PropTypes.number,
        id: PropTypes.number,
        contour: PropTypes.oneOfType([
          GeoPropTypes.FeatureCollection,
          PropTypes.shape({
            type: PropTypes.string,
            features: PropTypes.array, // eslint-disable-line react/forbid-prop-types
          }),
        ]),
        contour_summary: PropTypes.oneOfType([
          GeoPropTypes.FeatureCollection,
          PropTypes.shape({
            type: PropTypes.string,
            features: PropTypes.array, // eslint-disable-line react/forbid-prop-types
          }),
        ]),
      })
    ),
  }),
  observations: PropTypes.arrayOf(
    PropTypes.oneOfType([
      GeoPropTypes.FeatureCollection,
      PropTypes.shape({
        type: PropTypes.string,
        features: PropTypes.array, // eslint-disable-line react/forbid-prop-types
      }),
    ])
  ),
  options: PropTypes.shape({
    skymap: PropTypes.bool,
    sources: PropTypes.bool,
    galaxies: PropTypes.bool,
    instrument: PropTypes.bool,
    observations: PropTypes.bool,
  }),
  height: PropTypes.number,
  width: PropTypes.number,
  rotation: PropTypes.arrayOf(PropTypes.number),
  setRotation: PropTypes.func,
  selectedFields: PropTypes.arrayOf(PropTypes.number),
  setSelectedFields: PropTypes.func,
  airmass_threshold: PropTypes.number,
  projectionType: PropTypes.string,
};

GeoJSONGlobePlot.defaultProps = {
  skymap: null,
  sources: null,
  galaxies: null,
  instrument: null,
  observations: null,
  options: {
    skymap: false,
    sources: false,
    galaxies: false,
    instrument: false,
    observations: false,
  },
  height: 600,
  width: 600,
  rotation: null,
  setRotation: () => {},
  selectedFields: [],
  setSelectedFields: () => {},
  airmass_threshold: 2.5,
  projectionType: "orthographic",
};

export default LocalizationPlot;
